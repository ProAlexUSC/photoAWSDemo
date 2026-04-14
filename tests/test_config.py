import os
from unittest import mock

import pytest


def test_is_local_defaults_to_true():
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("LOCAL_DEV", None)
        from importlib import reload

        import common.config

        reload(common.config)
        assert common.config.is_local() is True


def test_is_local_false():
    with mock.patch.dict(os.environ, {"LOCAL_DEV": "false"}):
        from importlib import reload

        import common.config

        reload(common.config)
        assert common.config.is_local() is False


def test_get_database_url_reads_env():
    with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/testdb"}):
        from importlib import reload

        import common.config

        reload(common.config)
        assert common.config.get_database_url() == "postgresql://test:test@localhost/testdb"


def test_get_database_url_missing_raises():
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("DATABASE_URL", None)
        from importlib import reload

        import common.config

        reload(common.config)
        with pytest.raises(KeyError):
            common.config.get_database_url()
