# Brazilian E-Commerce Dataset (Olist, via Kaggle)

Real-world e-commerce data from the Brazilian marketplace Olist —
~100,000 orders, customers, sellers, products, payments, reviews, and
geolocation across 2016–2018.

## Setup (one-time)

1. Download the dataset from
   [Kaggle: Brazilian E-Commerce by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).
2. Unzip into this directory so it looks like:
   ```
   datasets/brazilian_ecommerce/csv/
       olist_orders_dataset.csv
       olist_order_items_dataset.csv
       olist_products_dataset.csv
       olist_customers_dataset.csv
       olist_sellers_dataset.csv
       olist_order_payments_dataset.csv
       olist_order_reviews_dataset.csv
       olist_geolocation_dataset.csv
       product_category_name_translation.csv
   ```
3. Run the loader:
   ```bash
   python datasets/brazilian_ecommerce/load_brazilian.py
   ```
4. Re-index schema embeddings:
   ```bash
   python -m src.retrieval.bootstrap --source brazilian_ecommerce
   ```

## Tables

| Table                          | Description                                |
|--------------------------------|--------------------------------------------|
| olist_orders                   | Order header (status, timestamps)          |
| olist_order_items              | Order line items (price, freight, qty)     |
| olist_products                 | Product catalog                            |
| olist_customers                | Customer master                            |
| olist_sellers                  | Seller master                              |
| olist_payments                 | Payment records                            |
| olist_reviews                  | Review scores + comments                   |
| olist_geolocation              | Brazilian zip code → lat/lng               |
| product_category_translation   | Portuguese → English category names        |

## Try a query

```bash
curl -X POST localhost:8000/api/query -d '{
  "query": "按州统计平均订单金额",
  "source": "brazilian_ecommerce"
}'
```
