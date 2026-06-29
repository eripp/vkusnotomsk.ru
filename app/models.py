import enum
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric,
    String, Text, Time, BigInteger, JSON, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ─── Enums ────────────────────────────────────────────────────────────────────

class OrderStatus(str, enum.Enum):
    new = "new"
    accepted = "accepted"
    delivery = "delivery"
    done = "done"
    canceled = "canceled"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    refunded = "refunded"


class PaymentMethod(str, enum.Enum):
    online = "online"
    terminal = "terminal"
    cash = "cash"


class OtpChannel(str, enum.Enum):
    max = "max"   # заглушка (мессенджер не шлёт код по номеру) — код показывается на экране
    tg = "tg"     # Telegram Gateway — рабочая доставка кода по номеру
    sms = "sms"   # SMS.RU — доставка по номеру (требует подключённого отправителя)
    vk = "vk"     # заглушка — код показывается на экране


class PromoType(str, enum.Enum):
    discount = "discount"
    cashback = "cashback"
    referral = "referral"


class CashbackTxType(str, enum.Enum):
    earn = "earn"
    spend = "spend"
    manual = "manual"
    withdraw = "withdraw"


class ScheduleEntryType(str, enum.Enum):
    weekday = "weekday"
    date = "date"


# ─── Catalog ──────────────────────────────────────────────────────────────────

class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    icon: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    show_icon: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")


class ProductGroup(Base):
    __tablename__ = "product_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug_base: Mapped[str] = mapped_column(String(220), unique=True)
    default_product_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("products.id", use_alter=True, name="fk_group_default_product"), nullable=True
    )

    products: Mapped[list["Product"]] = relationship(
        "Product", back_populates="group", foreign_keys="Product.group_id"
    )
    default_product: Mapped[Optional["Product"]] = relationship(
        "Product", foreign_keys=[default_product_id], post_update=True
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("categories.id"))
    group_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("product_groups.id"), nullable=True
    )
    variant_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    composition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer)  # rubles
    weight: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    shelf_life: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    storage_cond: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    kcal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    protein: Mapped[Optional[float]] = mapped_column(Numeric(5, 1), nullable=True)
    fat: Mapped[Optional[float]] = mapped_column(Numeric(5, 1), nullable=True)
    carbs: Mapped[Optional[float]] = mapped_column(Numeric(5, 1), nullable=True)
    label_popular: Mapped[bool] = mapped_column(Boolean, default=False)
    label_halal: Mapped[bool] = mapped_column(Boolean, default=False)
    label_post: Mapped[bool] = mapped_column(Boolean, default=False)
    label_new: Mapped[bool] = mapped_column(Boolean, default=False)
    label_kids: Mapped[bool] = mapped_column(Boolean, default=False)
    label_vegan: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped["Category"] = relationship("Category", back_populates="products")
    group: Mapped[Optional["ProductGroup"]] = relationship(
        "ProductGroup", back_populates="products", foreign_keys=[group_id]
    )
    images: Mapped[list["ProductImage"]] = relationship(
        "ProductImage", back_populates="product", order_by="ProductImage.sort_order"
    )
    order_items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="product")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    url: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship("Product", back_populates="images")


# ─── Users & Auth ─────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tg_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    phone_verified_via: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user")
    notification_settings: Mapped[Optional["NotificationSettings"]] = relationship(
        "NotificationSettings", back_populates="user", uselist=False
    )
    cashback_account: Mapped[Optional["CashbackAccount"]] = relationship(
        "CashbackAccount", back_populates="user", uselist=False
    )


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(200), default="")
    # Для кодов, которыми управляет внешний провайдер (i-dgtl): сам код мы не
    # генерируем и не храним — храним только uuid сессии верификации провайдера.
    provider_uuid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[OtpChannel] = mapped_column(Enum(OtpChannel))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class CartItem(Base):
    """Серверная корзина залогиненного пользователя. Хранит только product_id+qty;
    имя/цена/фото берутся из Product при чтении (без устаревших снапшотов)."""
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditEvent(Base):
    """Аудит важных действий: вход/отправка кода, админка, заказы, оплата.
    Только значимые события (не каждый HTTP-запрос) — IP + действие + результат."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), index=True)        # IPv4/IPv6
    action: Mapped[str] = mapped_column(String(40), index=True)    # напр. otp_send, admin_login
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | fail | blocked
    detail: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    email_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    email_promo: Mapped[bool] = mapped_column(Boolean, default=False)
    max_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    max_promo: Mapped[bool] = mapped_column(Boolean, default=False)
    tg_orders: Mapped[bool] = mapped_column(Boolean, default=True)
    tg_promo: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship("User", back_populates="notification_settings")


# ─── Orders ───────────────────────────────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(Text)
    address_lat: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    address_lon: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    zone_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("delivery_zones.id"), nullable=True)
    delivery_date: Mapped[date] = mapped_column(Date)
    slot_start: Mapped[time] = mapped_column(Time)
    slot_end: Mapped[time] = mapped_column(Time)
    schedule_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("schedule_entries.id"), nullable=True
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    cash_change_from: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.new)
    payment_status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    yookassa_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    promocode_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("promocodes.id"), nullable=True)
    discount_amount: Mapped[int] = mapped_column(Integer, default=0)
    cashback_spent: Mapped[int] = mapped_column(Integer, default=0)
    cashback_earned: Mapped[int] = mapped_column(Integer, default=0)
    delivery_price: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[int] = mapped_column(Integer)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[Optional["User"]] = relationship("User", back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="order")
    zone: Mapped[Optional["DeliveryZone"]] = relationship("DeliveryZone")
    promocode: Mapped[Optional["Promocode"]] = relationship("Promocode")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    product_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[int] = mapped_column(Integer)

    order: Mapped["Order"] = relationship("Order", back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="order_items")


class PendingOrder(Base):
    """Черновик онлайн-заказа: создаётся при оформлении с оплатой YooKassa,
    превращается в Order только после успешной оплаты (webhook). Если оплата не
    пришла — остаётся «висеть» и не засоряет реальные заказы."""
    __tablename__ = "pending_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)               # все данные будущего заказа
    yookassa_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # id после материализации
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Promo & Cashback ─────────────────────────────────────────────────────────

class Promocode(Base):
    __tablename__ = "promocodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    type: Mapped[PromoType] = mapped_column(Enum(PromoType))
    discount_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cashback_buyer_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cashback_owner_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    min_order_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    valid_until: Mapped[datetime] = mapped_column(DateTime)
    usage_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    owner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[owner_user_id])


class CashbackAccount(Base):
    __tablename__ = "cashback_accounts"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="cashback_account")


class CashbackTransaction(Base):
    __tablename__ = "cashback_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    type: Mapped[CashbackTxType] = mapped_column(Enum(CashbackTxType))
    amount: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("orders.id"), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    admin_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("admin_users.id"), nullable=True)

    user: Mapped["User"] = relationship("User")
    order: Mapped[Optional["Order"]] = relationship("Order")
    admin_user: Mapped[Optional["AdminUser"]] = relationship("AdminUser")


# ─── Delivery ─────────────────────────────────────────────────────────────────

class DeliveryZone(Base):
    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    polygon: Mapped[dict] = mapped_column(JSON)
    color: Mapped[str] = mapped_column(String(20), default="#3388ff")
    delivery_price: Mapped[int] = mapped_column(Integer)
    free_delivery_from: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_order_sum: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_type: Mapped[ScheduleEntryType] = mapped_column(Enum(ScheduleEntryType))
    weekday: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1=Mon … 7=Sun
    specific_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delivery_start: Mapped[time] = mapped_column(Time)
    delivery_end: Mapped[time] = mapped_column(Time)
    slot_interval_min: Mapped[int] = mapped_column(Integer, default=60)
    deadline_days_before: Mapped[int] = mapped_column(Integer, default=0)
    deadline_time: Mapped[time] = mapped_column(Time)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ─── Stories ──────────────────────────────────────────────────────────────────

class Story(Base):
    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    cover_image: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    slides: Mapped[list["StorySlide"]] = relationship(
        "StorySlide", back_populates="story", order_by="StorySlide.sort_order",
        cascade="all, delete-orphan",
    )


class StorySlide(Base):
    __tablename__ = "story_slides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(Integer, ForeignKey("stories.id"))
    image_url: Mapped[str] = mapped_column(String(500))
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    btn_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    btn_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    story: Mapped["Story"] = relationship("Story", back_populates="slides")


# ─── Settings & Admin ─────────────────────────────────────────────────────────

class SiteSetting(Base):
    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
