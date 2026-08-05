# Optionaler öffentlicher Proxy

Diese Dateien zeigen beispielhaft, wie FareGraph hinter einem bereits
vorhandenen Nginx-Ingress veröffentlicht werden kann.

Das Beispiel verwendet:

- LAN: `192.168.1.0/24`
- vorhandener Ingress: `192.168.1.215`
- FareGraph-Proxy: `192.168.1.216`
- Domain: `faregraph.example.com`

Vor der Verwendung müssen diese Werte an das eigene Netzwerk angepasst werden:

1. `docker-compose.yml`: feste Adresse des Dienstes `public-proxy`
2. `internal.conf`: erlaubte IP-Adresse des vorhandenen Ingress-Proxys
3. `faregraph.conf`: LAN, Domain, Zertifikatspfade und Proxy-Ziel

`faregraph.conf` wird als zusätzlicher virtueller Host in den vorhandenen
Ingress-Nginx eingebunden. `internal.conf` gehört in den separaten
`faregraph-public-proxy`, der das externe `br0`-Netz mit dem privaten
Compose-Netz von FareGraph verbindet.

Der optionale Dienst wird so gestartet:

```bash
docker compose --profile public-proxy up -d
```

FareGraph besitzt keine eigene Benutzerverwaltung. Für öffentlichen Zugriff
sind HTTPS und eine zusätzliche Authentifizierung erforderlich. Das Beispiel
verwendet HTTP Basic Authentication. Zertifikate, private Schlüssel und die
Datei `.faregraph.htpasswd` dürfen niemals in Git eingecheckt werden.
