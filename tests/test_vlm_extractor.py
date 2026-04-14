from unittest import mock


def test_vlm_extractor_writes_result_to_db():
    from vlm_extractor.handler import handler

    with mock.patch("vlm_extractor.handler.get_connection") as mock_conn:
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = handler({"photo_id": 42}, None)

        assert result["photo_id"] == 42
        assert result["status"] == "extracted"
        cur.execute.assert_called_once()
        args = cur.execute.call_args[0]
        assert "UPDATE photos SET vlm_result" in args[0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()
