from sqlalchemy import Column, Integer, String, Numeric
from sqlalchemy.orm import declarative_base

# 创建所有模型类的基类
Base = declarative_base()

class Product(Base):
    __tablename__ = 'products'  # 数据库中的表名

    # 定义列
    id = Column(Integer, primary_key=True)
    product_code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    # 假设用DECIMAL(10, 2)来存储价格
    price = Column(Numeric(10, 2), nullable=False)

    def __repr__(self):
        return f"<Product(name='{self.name}')>"