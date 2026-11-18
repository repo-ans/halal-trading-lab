from sqlalchemy import BigInteger, DateTime, Identity, Numeric
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Money and prices: NUMERIC(18,4) <-> Decimal. Never float.
Money = Numeric(18, 4)
# All timestamps: TIMESTAMPTZ, stored in UTC.
UTCDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


def pk_column():
    return mapped_column(BigInteger, Identity(always=False), primary_key=True)
