"""Unit tests for the assistant service (interpret_request + execute_plan)."""
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.assistant import (
    interpret_request,
    execute_plan,
    _sanitize_filename,
    _resolve_export_path,
)

VALID_PLAN_JSON = json.dumps({
    "steps": [
        {"action": "search", "params": {"query": "Sandeep Claude"}},
        {"action": "summarize", "params": {"focus": "what Sandeep said"}},
        {"action": "save_markdown", "params": {"filename": "Sandeep-Claude.md"}},
    ]
})

MULTI_SEARCH_PLAN = json.dumps({
    "steps": [
        {"action": "search", "params": {"query": "budget review"}},
        {"action": "search", "params": {"query": "Q4 planning"}},
        {"action": "summarize", "params": {}},
        {"action": "save_markdown", "params": {}},
    ]
})

SAVE_ONLY_PLAN = json.dumps({
    "steps": [
        {"action": "search", "params": {"query": "meeting notes"}},
        {"action": "save_markdown", "params": {"filename": "notes.md"}},
    ]
})

SEARCH_ONLY_PLAN = json.dumps({
    "steps": [
        {"action": "search", "params": {"query": "status update"}},
    ]
})


class TestSanitizeFilename:
    def test_keeps_safe_chars(self):
        assert _sanitize_filename("Sandeep-Claude_discussion.md") == "Sandeep-Claude_discussion.md"

    def test_replaces_spaces(self):
        assert _sanitize_filename("my summary file.md") == "my-summary-file.md"

    def test_strips_path_separators(self):
        assert _sanitize_filename("../../etc/passwd") == "etc-passwd"

    def test_strips_backslashes(self):
        assert _sanitize_filename("..\\..\\Windows\\win.ini") == "Windows-win.ini"

    def test_truncates_long_names(self):
        long_name = "a" * 200 + ".md"
        result = _sanitize_filename(long_name)
        assert len(result) <= 128

    def test_handles_empty(self):
        assert _sanitize_filename("") == "summary"

    def test_handles_none(self):
        assert _sanitize_filename(None) == "summary"


class TestResolveExportPath:
    def test_path_within_export_dir(self, tmp_path):
        result = _resolve_export_path(str(tmp_path), "out.md")
        assert result == os.path.realpath(tmp_path / "out.md")

    def test_path_escape_detected(self, tmp_path):
        with pytest.raises(ValueError, match="escapes"):
            _resolve_export_path(str(tmp_path), "../outside.md")


class TestInterpretRequest:
    @pytest.mark.asyncio
    async def test_valid_request_returns_plan(self):
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = VALID_PLAN_JSON
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "find Sandeep discussing Claude and save",
                    "fake-key", "groq", "llama-3.3",
                )
        assert "steps" in plan
        assert plan["steps"][0]["action"] == "search"

    @pytest.mark.asyncio
    async def test_llm_error_returns_error(self):
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.side_effect = RuntimeError("API timeout")
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "error" in plan
        assert "API timeout" in plan["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self):
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = "not json at all"
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "error" in plan

    @pytest.mark.asyncio
    async def test_json_no_steps_key(self):
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = '{"wrong": "shape"}'
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "error" in plan

    @pytest.mark.asyncio
    async def test_unsupported_action_detected(self):
        bad_plan = json.dumps({"steps": [{"action": "delete_everything", "params": {}}]})
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = bad_plan
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "error" in plan
        assert "Unsupported" in plan["error"]

    @pytest.mark.asyncio
    async def test_summarize_before_search_rejected(self):
        bad_plan = json.dumps({"steps": [
            {"action": "summarize", "params": {"focus": "x"}},
            {"action": "search", "params": {"query": "x"}},
        ]})
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = bad_plan
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "error" in plan
        assert "must come after" in plan["error"]

    @pytest.mark.asyncio
    async def test_strips_code_fences(self):
        with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
            mock_cc.return_value = "```json\n" + VALID_PLAN_JSON + "\n```"
            with patch("services.assistant.resolve_model", return_value="llama3"):
                plan = await interpret_request(
                    "anything", "fake-key", "groq", "llama-3.3",
                )
        assert "steps" in plan


class TestExecutePlan:
    def _mock_search(self, db, user_id, query):
        return [{
            "transcript_id": 1,
            "title": "Test Meeting",
            "filename": "test.mp3",
            "matching_segments": [
                {"speaker": "Alice", "text": "Let's discuss the budget.", "start": 0.0, "end": 5.0},
                {"speaker": "Bob", "text": "Agreed, we need to cut costs.", "start": 5.0, "end": 10.0},
            ],
        }]

    def _empty_search(self, db, user_id, query):
        return []

    @pytest.mark.asyncio
    async def test_search_step_no_results(self):
        with patch("services.assistant.search_transcripts", self._empty_search):
            result = await execute_plan(
                MagicMock(), 1, json.loads(SEARCH_ONLY_PLAN),
                "fake-key", "groq", "llama-3.3",
            )
        assert result["ok"] is True
        assert "No matching" in result["result"]["summary"]

    @pytest.mark.asyncio
    async def test_full_plan_executes(self, tmp_path):
        export_dir = str(tmp_path)
        plan = json.loads(VALID_PLAN_JSON)
        mock_db = MagicMock()

        with patch("services.assistant.search_transcripts", self._mock_search):
            with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
                mock_cc.return_value = "Alice proposed budget cuts. Bob agreed."
                with patch("services.assistant.resolve_model", return_value="llama3"):
                    result = await execute_plan(
                        mock_db, 1, plan, "fake-key", "groq", "llama-3.3",
                        export_directory=export_dir,
                    )
        assert result["ok"] is True
        assert result["error"] is None
        assert "budget" in result["result"]["summary"].lower()
        assert result["result"]["file_path"] is not None
        assert os.path.exists(result["result"]["file_path"])

    @pytest.mark.asyncio
    async def test_save_with_no_export_dir_returns_preview(self):
        plan = json.loads(VALID_PLAN_JSON)
        mock_db = MagicMock()

        with patch("services.assistant.search_transcripts", self._mock_search):
            with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
                mock_cc.return_value = "Summary text"
                with patch("services.assistant.resolve_model", return_value="llama3"):
                    result = await execute_plan(
                        mock_db, 1, plan, "fake-key", "groq", "llama-3.3",
                    )
        assert result["ok"] is True
        assert result["result"]["file_path"] is None
        assert result["result"]["preview"] == "Summary text"

    @pytest.mark.asyncio
    async def test_export_dir_creation_fallback(self, tmp_path):
        plan = json.loads(VALID_PLAN_JSON)
        # Create a file where the directory would go — os.makedirs will fail
        blocked = tmp_path / "blocked"
        blocked.write_text("x")
        with patch("services.assistant.search_transcripts", self._mock_search):
            with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
                mock_cc.return_value = "Summary text"
                with patch("services.assistant.resolve_model", return_value="llama3"):
                    result = await execute_plan(
                        MagicMock(), 1, plan, "fake-key", "groq", "llama-3.3",
                        export_directory=str(blocked),
                    )
        assert result["ok"] is True
        assert result["result"]["file_path"] is None
        assert result["error"] is not None  # notes the failure

    @pytest.mark.asyncio
    async def test_llm_failure_mid_plan(self):
        plan = json.loads(VALID_PLAN_JSON)
        with patch("services.assistant.search_transcripts", self._mock_search):
            with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
                mock_cc.side_effect = RuntimeError("LLM crashed")
                with patch("services.assistant.resolve_model", return_value="llama3"):
                    result = await execute_plan(
                        MagicMock(), 1, plan, "fake-key", "groq", "llama-3.3",
                    )
        assert result["ok"] is False
        assert "LLM crashed" in result["error"]

    @pytest.mark.asyncio
    async def test_save_before_summarize_rejected(self):
        plan = json.loads(SAVE_ONLY_PLAN)
        with patch("services.assistant.search_transcripts", self._mock_search):
            result = await execute_plan(
                MagicMock(), 1, plan, "fake-key", "groq", "llama-3.3",
            )
        assert result["ok"] is False
        assert "summarize" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tmp_path):
        plan = json.loads(VALID_PLAN_JSON)
        plan["steps"][2]["params"]["filename"] = "../../etc/shadow"
        with patch("services.assistant.search_transcripts", self._mock_search):
            with patch("services.assistant.chat_completion", new_callable=AsyncMock) as mock_cc:
                mock_cc.return_value = "Summary text"
                with patch("services.assistant.resolve_model", return_value="llama3"):
                    result = await execute_plan(
                        MagicMock(), 1, plan, "fake-key", "groq", "llama-3.3",
                        export_directory=str(tmp_path),
                    )
        # Path traversal should be sanitized, not rejected — filename is cleaned
        # before resolution, so it becomes a safe name
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_empty_steps_returns_ok(self):
        with patch("services.assistant.search_transcripts", self._empty_search):
            result = await execute_plan(
                MagicMock(), 1, {"steps": []}, "fake-key", "groq", "llama-3.3",
            )
        assert result["ok"] is True
        assert result["result"]["summary"] == ""
