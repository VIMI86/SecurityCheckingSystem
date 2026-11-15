FROM postgres:16

COPY entrypoint.sh /usr/local/bin/sentinel-entrypoint.sh
COPY init-replication.sh /docker-entrypoint-initdb.d/10-replication.sh
RUN chmod 755 /usr/local/bin/sentinel-entrypoint.sh /docker-entrypoint-initdb.d/10-replication.sh

ENTRYPOINT ["sentinel-entrypoint.sh"]
