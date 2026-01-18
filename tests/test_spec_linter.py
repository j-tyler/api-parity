"""Tests for spec_linter module."""

from pathlib import Path

import pytest
import yaml

from api_parity.spec_linter import (
    LintMessage,
    LintResult,
    SpecLinter,
    SpecLinterError,
    format_lint_result_text,
)


class TestLintResult:
    """Tests for LintResult dataclass."""

    def test_add_error(self):
        """Test adding error messages."""
        result = LintResult()
        msg = LintMessage(level="error", code="test", message="Test error")
        result.add(msg)
        assert len(result.errors) == 1
        assert result.has_errors()

    def test_add_warning(self):
        """Test adding warning messages."""
        result = LintResult()
        msg = LintMessage(level="warning", code="test", message="Test warning")
        result.add(msg)
        assert len(result.warnings) == 1
        assert not result.has_errors()

    def test_add_info(self):
        """Test adding info messages."""
        result = LintResult()
        msg = LintMessage(level="info", code="test", message="Test info")
        result.add(msg)
        assert len(result.info) == 1
        assert not result.has_errors()

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = LintResult(total_operations=5, operations_with_links=3)
        result.add(LintMessage(level="error", code="e1", message="Error 1"))
        result.add(LintMessage(level="warning", code="w1", message="Warning 1"))
        result.add(LintMessage(level="info", code="i1", message="Info 1"))

        d = result.to_dict()
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1
        assert len(d["info"]) == 1
        assert d["summary"]["total_operations"] == 5
        assert d["summary"]["operations_with_links"] == 3
        assert d["summary"]["error_count"] == 1


class TestSpecLinter:
    """Tests for SpecLinter class."""

    def test_lint_valid_spec(self, tmp_path):
        """Test linting a valid spec with proper links."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {"item_id": "$response.body#/id"},
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/{item_id}": {
                    "get": {
                        "operationId": "getItem",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "valid.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        assert not result.has_errors()
        assert result.total_operations == 2
        assert result.operations_with_links == 2

    def test_lint_spec_with_invalid_link_target(self, tmp_path):
        """Test detecting invalid link targets."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {
                                        "operationId": "nonExistentOperation",
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "invalid_target.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        assert result.has_errors()
        assert any(e.code == "invalid-link-target" for e in result.errors)

    def test_lint_spec_with_no_links(self, tmp_path):
        """Test warning when spec has no explicit links."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array"}
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "no_links.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        assert not result.has_errors()
        assert any(w.code == "no-explicit-links" for w in result.warnings)

    def test_lint_spec_with_orphan_operations(self, tmp_path):
        """Test detecting isolated operations."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/{id}": {
                    "get": {
                        "operationId": "getItem",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/health": {
                    "get": {
                        "operationId": "healthCheck",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

        spec_file = tmp_path / "orphans.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # healthCheck should be isolated
        isolated_msgs = [m for m in result.info if m.code == "isolated-operation"]
        assert len(isolated_msgs) == 1
        assert isolated_msgs[0].operation_id == "healthCheck"

    def test_lint_spec_with_missing_response_schema(self, tmp_path):
        """Test warning when operations lack response schemas."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "delete": {
                        "operationId": "deleteItem",
                        "responses": {
                            "204": {
                                "description": "Deleted",
                                # No content/schema
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "no_schema.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        assert any(w.code == "missing-response-schema" for w in result.warnings)

    def test_lint_spec_with_non_200_links(self, tmp_path):
        """Test detecting links on non-200 status codes."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {"operationId": "getItem"},
                                },
                            },
                            "202": {
                                "description": "Accepted",
                                "links": {
                                    "GetStatus": {"operationId": "getStatus"},
                                },
                            },
                        },
                    }
                },
                "/items/{id}": {
                    "get": {
                        "operationId": "getItem",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
                "/status": {
                    "get": {
                        "operationId": "getStatus",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

        spec_file = tmp_path / "non_200_links.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # Should have info about non-200 links
        non_200_msgs = [m for m in result.info if m.code == "non-200-status-links"]
        assert len(non_200_msgs) == 1
        assert non_200_msgs[0].details["links"]

    def test_lint_spec_file_not_found(self, tmp_path):
        """Test error when spec file doesn't exist."""
        with pytest.raises(SpecLinterError) as exc_info:
            SpecLinter(tmp_path / "nonexistent.yaml")
        assert "not found" in str(exc_info.value)

    def test_lint_spec_invalid_yaml(self, tmp_path):
        """Test error when spec is invalid YAML."""
        spec_file = tmp_path / "invalid.yaml"
        with open(spec_file, "w") as f:
            f.write("invalid: [yaml: content\n")

        with pytest.raises(SpecLinterError) as exc_info:
            SpecLinter(spec_file)
        assert "parse" in str(exc_info.value).lower()

    def test_lint_empty_spec(self, tmp_path):
        """Test linting an empty spec file."""
        spec_file = tmp_path / "empty.yaml"
        with open(spec_file, "w") as f:
            f.write("")

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # Empty spec should have no operations and warn about no links
        assert result.total_operations == 0
        assert any(w.code == "no-explicit-links" for w in result.warnings)

    def test_lint_json_output(self, tmp_path):
        """Test JSON output format."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

        spec_file = tmp_path / "test.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()
        output = result.to_dict()

        # Verify JSON structure
        assert "errors" in output
        assert "warnings" in output
        assert "info" in output
        assert "summary" in output
        assert isinstance(output["summary"]["total_operations"], int)


class TestDuplicateLinkNames:
    """Tests for duplicate link name detection."""

    def test_detect_duplicate_link_names(self, tmp_path):
        """Test detection of duplicate link names in YAML file."""
        # Write YAML with duplicate keys manually (yaml.dump would dedupe)
        yaml_content = """
openapi: "3.0.3"
info:
  title: Test
  version: "1.0"
paths:
  /items:
    post:
      operationId: createItem
      responses:
        "201":
          description: Created
          links:
            GetItem:
              operationId: getItem
            GetItem:
              operationId: anotherOp
"""
        spec_file = tmp_path / "duplicate_links.yaml"
        with open(spec_file, "w") as f:
            f.write(yaml_content)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # Should detect duplicate link name
        dup_msgs = [e for e in result.errors if e.code == "duplicate-link-name"]
        assert len(dup_msgs) == 1
        assert dup_msgs[0].details["link_name"] == "GetItem"

    def test_detect_duplicate_link_names_4_space_indent(self, tmp_path):
        """Test detection of duplicate link names with 4-space indentation."""
        yaml_content = """
openapi: "3.0.3"
info:
    title: Test
    version: "1.0"
paths:
    /items:
        post:
            operationId: createItem
            responses:
                "201":
                    description: Created
                    links:
                        GetItem:
                            operationId: getItem
                        GetItem:
                            operationId: anotherOp
"""
        spec_file = tmp_path / "duplicate_links_4space.yaml"
        with open(spec_file, "w") as f:
            f.write(yaml_content)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # Should detect duplicate link name even with 4-space indentation
        dup_msgs = [e for e in result.errors if e.code == "duplicate-link-name"]
        assert len(dup_msgs) == 1
        assert dup_msgs[0].details["link_name"] == "GetItem"

    def test_no_false_positive_for_unique_links(self, tmp_path):
        """Test no false positive when link names are unique."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {"operationId": "getItem"},
                                    "UpdateItem": {"operationId": "updateItem"},
                                    "DeleteItem": {"operationId": "deleteItem"},
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "unique_links.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        # Should not have duplicate link errors
        dup_msgs = [e for e in result.errors if e.code == "duplicate-link-name"]
        assert len(dup_msgs) == 0


class TestLinkExpressionCoverage:
    """Tests for link expression type categorization."""

    def test_categorize_body_expressions(self, tmp_path):
        """Test categorization of body expressions."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {
                                            "item_id": "$response.body#/id",
                                            "nested": "$response.body#/data/nested_id",
                                        },
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "body_expr.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        coverage_msgs = [m for m in result.info if m.code == "link-expression-coverage"]
        assert len(coverage_msgs) == 1
        details = coverage_msgs[0].details
        assert details["body_expressions"] == 2
        assert "id" in details["body_fields"]
        assert "data/nested_id" in details["body_fields"]

    def test_categorize_header_expressions(self, tmp_path):
        """Test categorization of header expressions."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {
                                            "url": "$response.header.Location",
                                        },
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "header_expr.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        linter = SpecLinter(spec_file)
        result = linter.lint()

        coverage_msgs = [m for m in result.info if m.code == "link-expression-coverage"]
        assert len(coverage_msgs) == 1
        details = coverage_msgs[0].details
        assert details["header_expressions"] == 1
        assert "location" in details["header_names"]


class TestFormatLintResultText:
    """Tests for text output formatting."""

    def test_format_pass_result(self):
        """Test formatting a passing result."""
        result = LintResult(total_operations=3, operations_with_links=2)
        text = format_lint_result_text(result)

        assert "Total operations: 3" in text
        assert "Operations with links: 2" in text
        assert "Result: PASS" in text

    def test_format_fail_result(self):
        """Test formatting a failing result."""
        result = LintResult()
        result.add(LintMessage(level="error", code="test", message="Test error"))
        text = format_lint_result_text(result)

        assert "ERRORS (1)" in text
        assert "Result: FAIL" in text

    def test_format_with_warnings(self):
        """Test formatting a result with warnings."""
        result = LintResult()
        result.add(LintMessage(level="warning", code="test", message="Test warning"))
        text = format_lint_result_text(result)

        assert "WARNINGS (1)" in text
        assert "Result: PASS (with warnings)" in text


class TestWithRealFixture:
    """Tests using real test fixtures."""

    def test_lint_test_api_fixture(self):
        """Test linting the test_api.yaml fixture."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        linter = SpecLinter(spec_path)
        result = linter.lint()

        # test_api.yaml should be well-formed (no errors)
        assert not result.has_errors()
        # Should have multiple operations
        assert result.total_operations > 0
        # Should have links
        assert result.operations_with_links > 0
