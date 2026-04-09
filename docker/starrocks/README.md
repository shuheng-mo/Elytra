# StarRocks (optional)

StarRocks is an **optional** OLAP backend for Elytra. The main `docker-compose.yml`
only requires PostgreSQL — this stack lives in its own compose file so it can
be brought up or torn down without touching the rest of the system.

## Bring it up

```bash
docker compose -f docker/starrocks/docker-compose.starrocks.yml up -d
```

Wait until the FE is healthy:

```bash
docker compose -f docker/starrocks/docker-compose.starrocks.yml ps
# elytra-starrocks-fe should report (healthy)
```

## Register the BE with the FE (one-time)

StarRocks requires you to add the backend (BE) to the frontend (FE) cluster
exactly once. Use any MySQL client connected to FE port 9030:

```bash
docker exec -it elytra-starrocks-fe \
  mysql -h 127.0.0.1 -P 9030 -uroot \
  -e "ALTER SYSTEM ADD BACKEND 'starrocks-be:9050';"
```

Check it's joined:

```bash
docker exec -it elytra-starrocks-fe \
  mysql -h 127.0.0.1 -P 9030 -uroot \
  -e "SHOW BACKENDS\\G"
```

`Alive` should be `true`.

## Create the Elytra database + a sample table

```sql
CREATE DATABASE IF NOT EXISTS elytra;
USE elytra;

CREATE TABLE IF NOT EXISTS dwd_order_detail (
    order_id        BIGINT,
    user_id         BIGINT,
    user_level      VARCHAR(20),
    user_city       VARCHAR(50),
    category_l1     VARCHAR(50),
    brand           VARCHAR(100),
    quantity        INT,
    unit_price      DECIMAL(10,2),
    total_amount    DECIMAL(12,2),
    profit          DECIMAL(12,2),
    order_status    VARCHAR(20),
    order_date      DATE
)
DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 4
PROPERTIES ("replication_num" = "1");
```

You can mirror data from PostgreSQL with any standard ETL tool, or
INSERT a few rows by hand for smoke-testing.

## Re-index Elytra's schema embeddings

```bash
python -m src.retrieval.bootstrap --source ecommerce_starrocks
```

## Try a query

```bash
curl -X POST localhost:8000/api/query -d '{
  "query": "上个月各品类的销售额",
  "source": "ecommerce_starrocks"
}'
```

The generated SQL should be MySQL-flavored: `CONCAT(...)` instead of `||`,
`DATE_FORMAT(...)` instead of `TO_CHAR(...)`, `LIMIT m, n` instead of
`OFFSET ... LIMIT`.

## Tear it down

```bash
docker compose -f docker/starrocks/docker-compose.starrocks.yml down -v
```

## Troubleshooting

- **FE never goes healthy**: check `docker compose ... logs starrocks-fe`.
  StarRocks needs ~6 GB RAM to start; macOS Docker Desktop sometimes caps
  containers at 2 GB by default.
- **`Alive: false` for the BE**: the FE and BE must resolve each other by
  hostname. If you renamed the containers, update the `ADD BACKEND` command
  to match.
- **Connector reports `connected: false`**: check `STARROCKS_HOST` /
  `STARROCKS_PORT` env vars match the compose port mapping (default
  `localhost:9030`).
