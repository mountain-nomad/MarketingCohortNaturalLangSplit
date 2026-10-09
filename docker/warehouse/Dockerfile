# Demo warehouse: PostgreSQL seeded with the `ecommerce` sample from
# github.com/harryho/db-samples, fetched at BUILD time from a pinned commit and
# verified by SHA-256. The upstream repository has no license, so its SQL is
# intentionally not vendored into this repository.

FROM curlimages/curl:8.10.1 AS fetch
ARG DB_SAMPLES_COMMIT=9bd103fc8b12b1453c8d02571e413fc7fa65ecdb
ARG ECOMMERCE_SQL_SHA256=0d5c506624af90ce652b37a14062fd527a74cd1aea6a75189c125f01cc22b15b
WORKDIR /tmp/fetch
RUN curl -fsSL --retry 3 -o ecommerce.sql \
      "https://raw.githubusercontent.com/harryho/db-samples/${DB_SAMPLES_COMMIT}/pgsql/ecommerce.sql" \
 && echo "${ECOMMERCE_SQL_SHA256}  ecommerce.sql" | sha256sum -c -

FROM postgres:16.4-alpine
ENV POSTGRES_DB=ecommerce
# Loaded by the official entrypoint on first start, in file-name order.
COPY --from=fetch /tmp/fetch/ecommerce.sql /docker-entrypoint-initdb.d/10-ecommerce.sql
COPY initdb/20-readonly-role.sh /docker-entrypoint-initdb.d/20-readonly-role.sh
