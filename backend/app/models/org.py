"""Organisation structure: regions, branches, staff, loan products."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, Numeric, String, JSON)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(80), nullable=False)

    branches = relationship("Branch", back_populates="region")


class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    name = Column(String(80), nullable=False)

    region = relationship("Region", back_populates="branches")
    staff = relationship("Staff", back_populates="branch")


class Staff(Base):
    """Field/office staff. Salary + petty cash feed the Staff Net Margin metric."""
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    name = Column(String(120), nullable=False)
    role = Column(String(40), nullable=False, default="loan_officer")  # loan_officer | call_agent | branch_manager
    phone = Column(String(20))
    salary = Column(Numeric(12, 2), default=0)       # monthly KES
    petty_cash = Column(Numeric(12, 2), default=0)   # monthly KES float
    hire_date = Column(Date)
    active = Column(Boolean, default=True)

    branch = relationship("Branch", back_populates="staff")


class Product(Base):
    """Loan product configuration."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(20), nullable=False)
    interest_rate = Column(Numeric(6, 3), nullable=False, default=10.0)   # % over tenure
    interest_method = Column(String(20), default="flat")             # flat | reducing_balance
    tenure_value = Column(Integer, default=4)
    tenure_unit = Column(String(10), default="weeks")                # weeks | months
    repayment_frequency = Column(String(15), default="weekly")       # daily | weekly | monthly
    min_amount = Column(Numeric(12, 2), default=1000)
    max_amount = Column(Numeric(12, 2), default=100000)
    min_age = Column(Integer, default=18)
    max_age = Column(Integer, default=65)
    penalty_rate = Column(Numeric(6, 3), default=1.0)               # % per overdue period
    rules = Column(JSON, default=dict)                               # extensible eligibility rules
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
