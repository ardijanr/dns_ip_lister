{
  description = "Cached DNS blocklist server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSystems = lib.genAttrs systems;

      mkPackage =
        { pkgs
        , name ? "dns-ip-lister"
        , domains ? null
        , dnsServers ? [ "1.1.1.1" "8.8.8.8" "9.9.9.9" ]
        , bind ? "0.0.0.0"
        , port ? 8080
        , interval ? 3600
        }:
        let
          python = pkgs.python3.withPackages (ps: [
            ps.dnspython
          ]);

          resolvedDomains = if domains != null then domains else [ ];

          args =
            [
              "--bind ${lib.escapeShellArg bind}"
              "--port ${toString port}"
              "--domains ${lib.escapeShellArg (builtins.toJSON resolvedDomains)}"
              "--dns_servers ${lib.escapeShellArg (builtins.toJSON dnsServers)}"
              "--interval ${toString interval}"
            ];
        in
        pkgs.writeShellApplication {
          inherit name;

          runtimeInputs = [
            python
          ];

          text = ''
            exec python ${./main.py} ${lib.concatStringsSep " " args} "$@"
          '';
        };
    in
    {
      lib = {
        inherit mkPackage;
      };

      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };

          app = mkPackage {
            inherit pkgs;
          };
        in
        {
          default = app;
          mikrotik-youtube-blocklist = app;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program =
            "${self.packages.${system}.default}/bin/dns-ip-lister";
        };
      });
    };
}
