FROM python:3.12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iptables iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/firewall-entry.sh /usr/local/bin/firewall-entry.sh
RUN chmod 755 /usr/local/bin/firewall-entry.sh

ENTRYPOINT ["/usr/local/bin/firewall-entry.sh"]
