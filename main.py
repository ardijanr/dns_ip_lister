#!/usr/bin/env python3

import argparse
import asyncio
import ipaddress
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dns.resolver
import dns.asyncresolver


DEFAULT_DNS_SERVERS = (
    "1.1.1.1",
    "1.0.0.1",
    "2606:4700:4700::1111",
    "2606:4700:4700::1001",
    "9.9.9.9",
    "149.112.112.112",
    "2620:fe::fe",
    "2620:fe::9",
    "103.247.36.36",
    "103.247.37.37",
    "103.247.36.9",
    "103.247.37.9",
    "208.67.222.222",
    "208.67.220.220",
    "2620:0:ccc::2",
    "2620:0:ccd::2",
    "8.8.8.8",
    "8.8.4.4",
    "2001:4860:4860::8888",
    "2001:4860:4860::8844",
    "64.6.64.6",
    "64.6.65.6",
    "2620:74:1b::1:1",
    "2620:74:1c::2:2",
    "8.26.56.26",
    "8.20.247.20",
    "195.46.39.39",
    "195.46.39.40",
    "80.80.80.80",
    "80.80.81.81",
    "76.76.19.19",
    "76.223.122.150",
    "2602:fcbc::ad",
    "2602:fcbc:2::ad",
    "77.88.8.8",
    "77.88.8.1",
    "2a02:6b8::feed:0ff",
    "2a02:6b8:0:1::feed:0ff",
    "91.239.100.100",
    "89.233.43.71",
    "2001:67c:28a4::",
    "2a01:3a0:53:53::",
    "74.82.42.42",
    "2001:470:20::2",
    "156.154.70.1",
    "156.154.71.1",
    "2610:a1:1018::1",
    "2610:a1:1019::1",
    "1.2.4.8",
    "210.2.4.8",
    "240c::6666",
    "240c::6644",
    "223.5.5.5",
    "223.6.6.6",
    "119.29.29.29",
    "119.28.28.28",
    "101.226.4.6",
    "218.30.118.6",
    "94.140.14.140",
    "94.140.14.141",
    "2a10:50c0::1:ff",
    "2a10:50c0::2:ff",
    "45.90.28.167",
    "45.90.30.167",
    "2a07:a8c0::82:86df",
    "2a07:a8c1::82:86df",
    "76.76.2.0",
    "76.76.10.0",
    "2606:1a40::",
    "2606:1a40:1::",
    "198.54.117.10",
    "198.54.117.11",
    "2620:119:35::35",
    "2620:119:53::53",
    "95.85.95.85",
    "2.56.220.2",
    "2a03:90c0:999d::1",
    "2a03:90c0:9992::1",
    "149.112.121.10",
    "149.112.122.10",
    "2620:10A:80BB::10",
    "2620:10A:80BC::10",
    "75.75.75.75",
    "75.75.76.76",
    "2001:558:feed::1",
    "2001:558:feed::2",
    "84.200.69.80",
    "84.200.70.40",
    "2001:1608:10:25::1c04:b12f",
    "2001:1608:10:25::9249:d69b",
)

REFRESH_INTERVAL = 600
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_CONCURRENT_QUERIES = 256
DNS_TIMEOUT = 1.5
DNS_LIFETIME = 1.5
COMMON_SUBDOMAIN_PREFIXES = (
    "www",
    "m",
)


cache = {}
cache_lock = threading.Lock()


def valid_resolver(address):
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    return not parsed.is_unspecified


def local_dns():
    try:
        return [
            address
            for line in open("/etc/resolv.conf", encoding="utf-8").read().splitlines()
            for address in [line.split()[1]]
            if line.startswith("nameserver ")
            if valid_resolver(address)
        ]
    except OSError:
        return []


def parse_list_argument(value, argument_name):
    try:
        items = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"{argument_name} must be a JSON array") from exc

    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise argparse.ArgumentTypeError(f"{argument_name} must be a JSON array of strings")

    return list(
        dict.fromkeys(
            item.strip()
            for item in items
            if item.strip()
            and (
                argument_name in {"--domains", "--domain_sources"}
                or valid_resolver(item.strip())
            )
        )
    )


def parse_domain_text(content):
    return list(
        dict.fromkeys(
            line.strip().rstrip(".")
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    )


def expand_common_domains(configured_domains):
    expanded = []

    for domain in configured_domains:
        normalized = domain.rstrip(".")
        expanded.append(normalized)
        expanded.extend(f"{prefix}.{normalized}" for prefix in COMMON_SUBDOMAIN_PREFIXES)

    return list(dict.fromkeys(expanded))


def mark_addresses_seen(addresses, now):
    with cache_lock:
        for address in addresses:
            cache[address] = now


def expire_stale_addresses(now):
    cutoff = now - RETENTION_SECONDS

    with cache_lock:
        stale_addresses = [address for address, last_seen in cache.items() if last_seen < cutoff]

        for address in stale_addresses:
            del cache[address]

        return len(cache)


def snapshot_addresses():
    with cache_lock:
        return list(cache)


def build_resolver(server):
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.timeout = DNS_TIMEOUT
    resolver.lifetime = DNS_LIFETIME
    return resolver


async def resolve(name, resolver, semaphore):
    addresses = []

    async with semaphore:
        for record_type in ("A", "AAAA"):
            try:
                answer = await resolver.resolve(name, record_type)

                addresses += [
                    normalized
                    for record in answer
                    for normalized in [str(ipaddress.ip_address(str(record)))]
                    if valid_resolver(normalized)
                ]
            except dns.exception.DNSException:
                pass

    return addresses


async def resolve_all(names, servers):
    if not names or not servers:
        return set()

    semaphore = asyncio.Semaphore(min(MAX_CONCURRENT_QUERIES, len(names) * len(servers)))
    resolvers = {server: build_resolver(server) for server in servers}
    tasks = [
        asyncio.create_task(resolve(name, resolvers[server], semaphore))
        for name in names
        for server in servers
    ]

    addresses = set()

    for result in await asyncio.gather(*tasks):
        addresses.update(result)

    return addresses


async def update_once(configured_domains, dns_servers):
    now = time.time()
    names = expand_common_domains(configured_domains)
    servers = list(dict.fromkeys(local_dns() + list(DEFAULT_DNS_SERVERS) + dns_servers))

    print(
        f"Refreshing {len(names)} domains via {len(servers)} resolvers",
        flush=True,
    )

    addresses = await resolve_all(names, servers)

    if addresses:
        mark_addresses_seen(addresses, now)
        total_addresses = expire_stale_addresses(now)
        print(
            f"Observed {len(addresses)} addresses; serving {total_addresses} cached addresses",
            flush=True,
        )
    else:
        total_addresses = expire_stale_addresses(now)
        print(
            f"Refresh produced no addresses; serving {total_addresses} cached addresses",
            flush=True,
        )


async def update_loop(configured_domains, dns_servers, interval):
    while True:
        await update_once(configured_domains, dns_servers)
        await asyncio.sleep(interval)


def sorted_blocklist(addresses):
    if not addresses:
        return b""

    return (
        "\n".join(
            sorted(
                addresses,
                key=lambda value: (
                    ipaddress.ip_address(value).version,
                    int(ipaddress.ip_address(value)),
                ),
            )
        )
        + "\n"
    ).encode()


def update(names, dns_servers, interval):
    asyncio.run(update_loop(names, dns_servers, interval))


def handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return

            content = sorted_blocklist(snapshot_addresses())

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--domains", default="[]")
    parser.add_argument("--dns_servers", default=json.dumps(list(DEFAULT_DNS_SERVERS)))
    parser.add_argument("--interval", type=int, default=REFRESH_INTERVAL)
    args = parser.parse_args()

    names = parse_list_argument(args.domains, "--domains")
    dns_servers = parse_list_argument(args.dns_servers, "--dns_servers")

    # DNS happens in the background, so it cannot delay HTTP startup.
    threading.Thread(
        target=update,
        args=(names, dns_servers, args.interval),
        daemon=True,
    ).start()

    print(
        f"Listening on {args.bind}:{args.port}",
        flush=True,
    )

    server = ThreadingHTTPServer(
        (args.bind, args.port),
        handler(),
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
