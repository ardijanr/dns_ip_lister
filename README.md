Blocking domains on mikrotik routers was not reliable enough... Made a small helper python server that can list domains so that you can write a simple script on mikrotik routers to block them by blocking the ip.

Example on running the python http server.
```
python3 main.py --domains '["youtube.com","googlevideo.com","ytimg.com"]' --dns_servers '["1.1.1.1","8.8.8.8","9.9.9.9"]' --port 8080 --interval 60
```

Example importing the flake into your own system flake and running it as a service to list youtube DNS entries.
```
{ pkgs, inputs, ... }:

let
  dnsListerApp = inputs.dns_ip_lister.lib.mkPackage {
    inherit pkgs;
    domains = [
      "youtube.com"
      "googlevideo.com"
      "ytimg.com"
      "youtubei.googleapis.com"
      "youtube.googleapis.com"
      "youtu.be"
    ];
    dnsServers = [
      "1.1.1.1"
      "8.8.8.8"
      "9.9.9.9"
    ];
    port = 9301;
    interval = 3600;
  };
in
{
  networking.firewall.allowedTCPPorts = [ 9301 ];

  systemd.services.dns_ip_lister = {
    description = "DNS blocklist server";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      ExecStart = "${dnsListerApp}/bin/dns-ip-lister";
      Restart = "always";
      RestartSec = 5;
      DynamicUser = true;
    };
  };
}
```
