from unittest import mock


def test_mark_complete_sets_batch_completed():
    from mark_complete.handler import handler

    with (
        mock.patch("mark_complete.handler.get_connection") as mock_conn,
        mock.patch("mark_complete.handler.PgBatchManager") as MockManager,
    ):
        conn = mock.MagicMock()
        mock_conn.return_value = conn
        instance = MockManager.return_value

        result = handler({"batch_id": 1}, None)

        assert result == {"batch_id": 1, "status": "completed"}
        instance.mark_batch_complete.assert_called_once_with(1)
        conn.commit.assert_called_once()
