# Run this once (or any time you add/change a model) to create app.db
# with the current table definitions: python init_db.py

from db import Base, engine
import models  # noqa: F401  (import registers the model classes with Base)

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tables created:", list(Base.metadata.tables.keys()))
