"""Provision synthetic customers. Secrets are written once to a local private file."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from app import schema as s


def seed(conn, credentials):
    def put(table, **data):
        conn.execute(insert(table).values(**data).on_conflict_do_nothing())

    for tenant, product, workflow in [
        ("tob", "pA1", "sales"),
        ("toc", "pB1", "support"),
    ]:
        put(
            s.tenants,
            id=tenant,
            name="甲企业" if tenant == "tob" else "乙企业",
            company_ref=tenant + "@1",
        )
        put(
            s.products, tenant=tenant, id=product, name="A1", version_ref=product + "@1"
        )
        put(
            s.accounts,
            tenant=tenant,
            id="mock",
            product_id=product,
            capabilities=["order.lookup", "channel.reply"],
        )
        for customer, conversation in (
            [("c1", "cv1"), ("c3", "cv3")]
            if tenant == "tob"
            else [("u2", "cv2"), ("u4", "cv4")]
        ):
            put(
                s.customers,
                tenant=tenant,
                id=customer,
                name="模拟客户 " + customer,
                profile={"language": "zh-CN"},
            )
            put(
                s.conversations,
                tenant=tenant,
                id=conversation,
                customer_id=customer,
                account_id="mock",
                external_ref=conversation,
                permitted=True,
            )
            put(
                s.leads,
                tenant=tenant,
                id="lead-" + customer,
                customer_id=customer,
                product_id=product,
            )
        for actor, roles in [
            ("business", ["business"]),
            (
                "admin",
                [
                    "config",
                    "publish",
                    "operator",
                    "seat",
                    "correct",
                    "observe",
                    "model_admin",
                ],
            ),
            ("inbound", ["adapter"]),
        ]:
            name = f"{tenant}-{actor}"
            put(
                s.identities,
                tenant=tenant,
                id=name,
                roles=roles,
                products=[product],
                token_hash=hashlib.sha256(credentials[name].encode()).hexdigest(),
            )
    for order, customer, status in [("O7", "u2", "已发货"), ("O8", "u4", "备货中")]:
        put(
            s.orders,
            tenant="toc",
            id=order,
            customer_id=customer,
            product_id="pB1",
            shipping=status,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens-file", default=".runtime/dev-tokens.json")
    path = Path(parser.parse_args().tokens_file)
    path.parent.mkdir(exist_ok=True, parents=True)
    if not path.exists():
        data = {
            f"{t}-{a}": secrets.token_urlsafe(32)
            for t in ("tob", "toc")
            for a in ("admin", "business", "inbound")
        }
        with open(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as f:
            json.dump(data, f, indent=2)
    with create_engine(
        os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True
    ).begin() as conn:
        seed(conn, json.loads(path.read_text()))
    print("Seeded mock tenants; local credentials file:", path)


if __name__ == "__main__":
    main()
