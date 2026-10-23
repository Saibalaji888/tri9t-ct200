
from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint
from app.database import Base


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(Integer, unique=True, nullable=False, index=True)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    ingested_at = Column(String, nullable=False)


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("version", "node_id", name="uq_version_node"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version = Column(Integer, ForeignKey("document_versions.version"), nullable=False, index=True)
    node_id = Column(String, nullable=False, index=True)
    logical_id = Column(String, nullable=True, index=True)
    parent_id = Column(String, nullable=True)
    heading = Column(String, nullable=False)
    number = Column(String, nullable=True)
    level = Column(Integer, nullable=False, default=0)
    body_text = Column(Text, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    source_order = Column(Integer, nullable=False, default=0)
    content_hash = Column(String, nullable=True)


class Selection(Base):
    __tablename__ = "selections"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(Integer, ForeignKey("document_versions.version"), nullable=False)
    node_id = Column(String, nullable=False)
    logical_id = Column(String, nullable=True)
    content_hash = Column(String, nullable=True)
    label = Column(String, nullable=True)
    created_at = Column(String, nullable=False)


class Generation(Base):
    __tablename__ = "generations"

    id = Column(Integer, primary_key=True, index=True)
    selection_id = Column(Integer, ForeignKey("selections.id"), nullable=False)
    version = Column(Integer, nullable=False)
    output_path = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    status = Column(String, nullable=False, default="completed")
