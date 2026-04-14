from unittest import mock


def test_tagger_writes_tags_to_db():
    from tagger.handler import handler

    with mock.patch("tagger.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = handler({"photo_id": 42}, None)

        assert result["photo_id"] == 42
        assert result["status"] == "tagged"
        cur.execute.assert_called_once()
        args = cur.execute.call_args[0]
        assert "UPDATE photos SET tags" in args[0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()
