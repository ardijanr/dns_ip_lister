#!/usr/bin/env python3

import argparse
import ipaddress
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dns.resolver


DEFAULT_DNS_SERVERS = (
    "1.1.1.1",
    "8.8.8.8",
    "9.9.9.9",
)

REFRESH_INTERVAL = 3600


cache = b""
cache_lock = threading.Lock()


def local_dns():
    try:
        return [
            line.split()[1]
            for line in open("/etc/resolv.conf", encoding="utf-8").read().splitlines()
            if line.startswith("nameserver ")
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

    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


def set_cache(content):
    global cache

    with cache_lock:
        cache = content


def get_cache():
    with cache_lock:
        return cache


def resolve(name, server):
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.timeout = 2
    resolver.lifetime = 2

    addresses = []

    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(name, record_type)

            addresses += [
                str(ipaddress.ip_address(str(record)))
                for record in answer
            ]
        except dns.exception.DNSException:
            pass

    return addresses


def sorted_blocklist(addresses):
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
    while True:
        servers = list(dict.fromkeys(local_dns() + dns_servers))

        print(
            f"Refreshing {len(names)} domains via {', '.join(servers) or 'no resolvers'}",
            flush=True,
        )

        addresses = {
            address
            for name in names
            for server in servers
            for address in resolve(name, server)
        }

        if addresses:
            content = sorted_blocklist(addresses)
            set_cache(content)
            print(f"Updated in-memory cache with {len(addresses)} addresses", flush=True)
        else:
            print("Refresh produced no addresses; keeping last in-memory blocklist", flush=True)

        time.sleep(interval)


def handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return

            content = get_cache()

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

    ThreadingHTTPServer(
        (args.bind, args.port),
        handler(),
    ).serve_forever()


if __name__ == "__main__":
    main()
