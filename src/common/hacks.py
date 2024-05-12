"""This module is where I put things that I'm not proud of."""

from pip._internal.exceptions import UnsupportedWheel, InvalidWheel
from pip._internal.network.lazy_wheel import dist_from_wheel_url
from pip._internal.network.session import PipSession

__all__ = ["create_session", "metadata_from_wheel_url"]


def create_session() -> PipSession:
    return PipSession()


def metadata_from_wheel_url(
    project_name: str, wheel_url: str, session: PipSession
) -> str | None:
    """Get metadata from a wheel URL.

    I could re-implement this myself, but... this is easier.
    """
    session = create_session()
    try:
        dist = dist_from_wheel_url(name=project_name, url=wheel_url, session=session)
    except (UnsupportedWheel, InvalidWheel):
        return None
    return dist.read_text("METADATA")
