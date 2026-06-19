"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-16
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # categories
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("icon", sa.String(200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
    )

    # product_groups (создаём без FK на products — добавим после)
    op.create_table(
        "product_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug_base", sa.String(220), nullable=False, unique=True),
        sa.Column("default_product_id", sa.Integer(), nullable=True),
    )

    # products
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("product_groups.id"), nullable=True),
        sa.Column("variant_label", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(220), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("composition", sa.Text(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("weight", sa.String(50), nullable=True),
        sa.Column("shelf_life", sa.String(100), nullable=True),
        sa.Column("storage_cond", sa.String(200), nullable=True),
        sa.Column("kcal", sa.Integer(), nullable=True),
        sa.Column("protein", sa.Numeric(5, 1), nullable=True),
        sa.Column("fat", sa.Numeric(5, 1), nullable=True),
        sa.Column("carbs", sa.Numeric(5, 1), nullable=True),
        sa.Column("label_popular", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("label_halal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("label_post", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("label_new", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("label_kids", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("label_vegan", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # добавляем FK на product_groups.default_product_id
    op.create_foreign_key(
        "fk_group_default_product",
        "product_groups", "products",
        ["default_product_id"], ["id"],
    )

    # product_images
    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("max_user_id", sa.String(100), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # otp_codes
    op.create_table(
        "otp_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False, index=True),
        sa.Column("code_hash", sa.String(200), nullable=False),
        sa.Column("channel", sa.Enum("max", "tg", name="otpchannel"), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
    )

    # notification_settings
    op.create_table(
        "notification_settings",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("email_orders", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("email_promo", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("max_orders", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("max_promo", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tg_orders", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tg_promo", sa.Boolean(), nullable=False, server_default="false"),
    )

    # admin_users
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_login", sa.DateTime(), nullable=True),
    )

    # promocodes
    op.create_table(
        "promocodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("type", sa.Enum("discount", "cashback", "referral", name="promotype"), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=True),
        sa.Column("cashback_buyer_pct", sa.Integer(), nullable=True),
        sa.Column("cashback_owner_pct", sa.Integer(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("min_order_amount", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("usage_limit", sa.Integer(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    # cashback_accounts
    op.create_table(
        "cashback_accounts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # delivery_zones
    op.create_table(
        "delivery_zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("polygon", sa.JSON(), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default=sa.text("'#3388ff'")),
        sa.Column("delivery_price", sa.Integer(), nullable=False),
        sa.Column("free_delivery_from", sa.Integer(), nullable=True),
        sa.Column("min_order_sum", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    # schedule_entries
    op.create_table(
        "schedule_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_type",
            sa.Enum("weekday", "date", name="scheduleentrytype"),
            nullable=False,
        ),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("specific_date", sa.Date(), nullable=True),
        sa.Column("delivery_start", sa.Time(), nullable=False),
        sa.Column("delivery_end", sa.Time(), nullable=False),
        sa.Column("slot_interval_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("deadline_days_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    # orders
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("address_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("address_lon", sa.Numeric(10, 7), nullable=True),
        sa.Column("zone_id", sa.Integer(), sa.ForeignKey("delivery_zones.id"), nullable=True),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("slot_start", sa.Time(), nullable=False),
        sa.Column("slot_end", sa.Time(), nullable=False),
        sa.Column("schedule_entry_id", sa.Integer(), sa.ForeignKey("schedule_entries.id"), nullable=True),
        sa.Column(
            "payment_method",
            sa.Enum("online", "terminal", "cash", name="paymentmethod"),
            nullable=False,
        ),
        sa.Column("cash_change_from", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("new", "accepted", "delivery", "done", "canceled", name="orderstatus"),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column(
            "payment_status",
            sa.Enum("pending", "paid", "refunded", name="paymentstatus"),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("yookassa_payment_id", sa.String(100), nullable=True),
        sa.Column("promocode_id", sa.Integer(), sa.ForeignKey("promocodes.id"), nullable=True),
        sa.Column("discount_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cashback_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cashback_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # order_items
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("product_price", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Integer(), nullable=False),
    )

    # cashback_transactions
    op.create_table(
        "cashback_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "type",
            sa.Enum("earn", "spend", "manual", "withdraw", name="cashbacktxtype"),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
    )

    # stories
    op.create_table(
        "stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("cover_image", sa.String(500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )

    # story_slides
    op.create_table(
        "story_slides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("image_url", sa.String(500), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_color", sa.String(20), nullable=True),
        sa.Column("btn_label", sa.String(100), nullable=True),
        sa.Column("btn_url", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    # site_settings
    op.create_table(
        "site_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default="''"),
    )

    # seed default site settings
    op.execute(
        """
        INSERT INTO site_settings (key, value) VALUES
          ('metrika_id', ''),
          ('webvisor', 'false'),
          ('ecom_enabled', 'false'),
          ('head_scripts', ''),
          ('body_scripts', ''),
          ('cashback_max_pct', '30'),
          ('yandex_verification', '')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("site_settings")
    op.drop_table("story_slides")
    op.drop_table("stories")
    op.drop_table("cashback_transactions")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("schedule_entries")
    op.drop_table("delivery_zones")
    op.drop_table("cashback_accounts")
    op.drop_table("promocodes")
    op.drop_table("admin_users")
    op.drop_table("notification_settings")
    op.drop_table("otp_codes")
    op.drop_table("users")
    op.drop_table("product_images")
    op.drop_constraint("fk_group_default_product", "product_groups", type_="foreignkey")
    op.drop_table("products")
    op.drop_table("product_groups")
    op.drop_table("categories")
    op.execute("DROP TYPE IF EXISTS otpchannel")
    op.execute("DROP TYPE IF EXISTS promotype")
    op.execute("DROP TYPE IF EXISTS scheduleentrytype")
    op.execute("DROP TYPE IF EXISTS paymentmethod")
    op.execute("DROP TYPE IF EXISTS orderstatus")
    op.execute("DROP TYPE IF EXISTS paymentstatus")
    op.execute("DROP TYPE IF EXISTS cashbacktxtype")
