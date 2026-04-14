from unittest import mock


def test_get_photo_ids_returns_list():
    from get_photo_ids.handler import handler

    with mock.patch("get_photo_ids.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        cur.fetchall.return_value = [(1,), (2,), (3,)]
        conn.cursor.return_value = cur
        mock_conn.return_value = conn
        result = handler({"batch_id": 1}, None)
        assert result == {"batch_id": 1, "photo_ids": [1, 2, 3]}
        cur.execute.assert_called_once()
