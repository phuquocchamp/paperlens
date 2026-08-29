"""Single declarative Base for every ORM model.

There is exactly ONE ``Base`` in PaperLens (§3: "Một Base duy nhất"). Its
``MetaData`` carries an explicit ``naming_convention`` — mandatory for stable
Alembic autogenerate, so index / constraint names never churn between runs and
the hand-written 0001 migration can spell the same names literally.

The ``ck`` template requires that every ``CheckConstraint`` be given an explicit
``name=`` (otherwise DDL compilation raises a KeyError on ``constraint_name``).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# SQLAlchemy naming convention. Keys map to constraint/index kinds; the
# templates must stay in sync with the names hand-written in 0001_initial.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The one declarative base. All models subclass this."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
