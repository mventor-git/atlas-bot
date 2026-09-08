"""
Base repository interface for Labor-Report.

Defines the abstract base class for all repositories
using the Repository pattern for data access abstraction.
"""

from abc import ABC, abstractmethod
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Abstract base repository providing CRUD interface.

    All concrete repositories should inherit from this class
    and implement the required methods.

    Type parameter T represents the entity type (e.g., Report, ReportItem).
    """

    @abstractmethod
    def get_by_id(self, entity_id: int) -> Optional[T]:
        """Retrieve an entity by its ID.

        Args:
            entity_id: The unique identifier of the entity.

        Returns:
            The entity if found, None otherwise.
        """
        ...

    @abstractmethod
    def get_all(self) -> list[T]:
        """Retrieve all entities.

        Returns:
            List of all entities.
        """
        ...

    @abstractmethod
    def add(self, entity: T) -> T:
        """Add a new entity.

        Args:
            entity: The entity to add (without ID).

        Returns:
            The entity with its assigned ID populated.
        """
        ...

    @abstractmethod
    def update(self, entity: T) -> T:
        """Update an existing entity.

        Args:
            entity: The entity with updated values (must have ID).

        Returns:
            The updated entity.
        """
        ...

    @abstractmethod
    def delete(self, entity_id: int) -> bool:
        """Delete an entity by its ID.

        Args:
            entity_id: The ID of the entity to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """Count all entities.

        Returns:
            Total number of entities.
        """
        ...
