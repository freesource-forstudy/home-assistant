"""Purge old data helper."""
from datetime import timedelta
import logging

import homeassistant.util.dt as dt_util

from .util import session_scope

_LOGGER = logging.getLogger(__name__)


def purge_old_data(instance, purge_days):
    """Purge events and states older than purge_days ago."""
    from .models import States, Events
    from sqlalchemy import func

    purge_before = dt_util.utcnow() - timedelta(days=purge_days)

    with session_scope(session=instance.get_session()) as session:
        # For each entity, the most recent state is protected from deletion
        # s.t. we can properly restore state even if the entity has not been
        # updated in a long time
        protected_states = session.query(States.state_id, States.event_id,
                                         func.max(States.last_updated)) \
                              .group_by(States.entity_id).subquery()

        protected_state_ids = session.query(States.state_id).join(
            protected_states, States.state_id == protected_states.c.state_id)\
            .subquery()

        deleted_rows = session.query(States) \
                              .filter((States.last_updated < purge_before)) \
                              .filter(~States.state_id.in_(
                                  protected_state_ids)) \
                              .delete(synchronize_session=False)
        _LOGGER.debug("Deleted %s states", deleted_rows)

        # We also need to protect the events belonging to the protected states.
        # Otherwise, if the SQL server has "ON DELETE CASCADE" as default, it
        # will delete the protected state when deleting its associated
        # event. Also, we would be producing NULLed foreign keys otherwise.

        protected_event_ids = session.query(States.event_id).join(
            protected_states, States.state_id == protected_states.c.state_id)\
            .filter(~States.event_id is not None).subquery()

        deleted_rows = session.query(Events) \
            .filter((Events.time_fired < purge_before)) \
            .filter(~Events.event_id.in_(
                protected_event_ids
            )) \
            .delete(synchronize_session=False)
        _LOGGER.debug("Deleted %s events", deleted_rows)

    # Execute sqlite vacuum command to free up space on disk
    _LOGGER.debug("DB engine driver: %s", instance.engine.driver)
    if instance.engine.driver == 'pysqlite':
        from sqlalchemy import exc

        _LOGGER.info("Vacuuming SQLite to free space")
        try:
            instance.engine.execute("VACUUM")
        except exc.OperationalError as err:
            _LOGGER.error("Error vacuuming SQLite: %s.", err)
