"""Integration tests for the mock server infrastructure.

These tests verify:
1. Mock servers start and respond correctly
2. Variant differences are observable (for testing comparison logic)
3. CRUD operations work for stateful chain testing
"""

import httpx
import pytest


class TestMockServerBasic:
    """Basic connectivity and response tests."""

    def test_health_check(self, mock_server_a):
        """Server responds to health check."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "timestamp" in data
            assert "uptime_seconds" in data

    def test_list_widgets_seeded_data(self, mock_server_a):
        """Server has seeded widget data."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/widgets")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 3
            assert len(data["widgets"]) == 3
            assert "X-Request-Id" in response.headers
            assert "X-Total-Count" in response.headers

    def test_get_user_profile_seeded(self, mock_server_a):
        """Server has seeded user data."""
        user_id = "00000000-0000-0000-0000-000000000001"
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get(f"/users/{user_id}/profile")
            assert response.status_code == 200
            data = response.json()
            assert data["username"] == "alice"
            assert "admin" in data["roles"]
            assert "scores" in data

    def test_list_widgets_filter_by_category(self, mock_server_a):
        """Filter widgets by category."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/widgets", params={"category": "gadgets"})
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert all(w["category"] == "gadgets" for w in data["widgets"])

    def test_list_widgets_filter_by_price_range(self, mock_server_a):
        """Filter widgets by price range."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/widgets", params={"min_price": 10, "max_price": 20})
            assert response.status_code == 200
            data = response.json()
            # Only "Super Wrench" at 15.50 is in range
            assert data["total"] == 1
            assert all(10 <= w["price"] <= 20 for w in data["widgets"])


class TestMockServerCRUD:
    """CRUD operations for stateful testing."""

    def test_create_widget(self, mock_server_a):
        """Create a new widget."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            widget_data = {
                "name": "Test Widget",
                "price": 19.99,
                "category": "gadgets",
                "tags": ["test", "new"],
            }
            response = client.post("/widgets", json=widget_data)
            assert response.status_code == 201
            data = response.json()

            assert "id" in data
            assert data["name"] == "Test Widget"
            assert data["category"] == "gadgets"
            assert "created_at" in data
            assert "Location" in response.headers

    def test_get_widget(self, mock_server_a):
        """Get a specific widget."""
        widget_id = "10000000-0000-0000-0000-000000000001"
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get(f"/widgets/{widget_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == widget_id
            assert data["name"] == "Gizmo Pro"
            assert "ETag" in response.headers

    def test_update_widget(self, mock_server_a):
        """Update an existing widget."""
        widget_id = "10000000-0000-0000-0000-000000000001"
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            update_data = {"price": 34.99}
            response = client.put(f"/widgets/{widget_id}", json=update_data)
            assert response.status_code == 200
            data = response.json()
            assert data["price"] == 34.99
            assert "updated_at" in data

    def test_delete_widget(self, mock_server_a):
        """Delete a widget."""
        # First create one to delete
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            create_response = client.post("/widgets", json={
                "name": "To Delete",
                "price": 1.00,
                "category": "parts",
            })
            widget_id = create_response.json()["id"]

            # Delete it
            response = client.delete(f"/widgets/{widget_id}")
            assert response.status_code == 204

            # Verify it's gone
            get_response = client.get(f"/widgets/{widget_id}")
            assert get_response.status_code == 404

    def test_create_order(self, mock_server_a):
        """Create an order."""
        user_id = "00000000-0000-0000-0000-000000000001"
        widget_id = "10000000-0000-0000-0000-000000000001"

        with httpx.Client(base_url=mock_server_a.base_url) as client:
            order_data = {
                "user_id": user_id,
                "items": [
                    {"widget_id": widget_id, "quantity": 2}
                ]
            }
            response = client.post("/orders", json=order_data)
            assert response.status_code == 201
            data = response.json()

            assert "id" in data
            assert data["user_id"] == user_id
            assert data["status"] == "pending"
            assert len(data["items"]) == 1
            assert "total" in data
            assert "X-Confirmation-Code" in response.headers


class TestMockServerErrors:
    """Error response handling."""

    def test_widget_not_found(self, mock_server_a):
        """404 for nonexistent widget."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/widgets/00000000-0000-0000-0000-000000000000")
            assert response.status_code == 404

    def test_user_not_found(self, mock_server_a):
        """404 for nonexistent user."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.get("/users/00000000-0000-0000-0000-000000000000/profile")
            assert response.status_code == 404

    def test_order_invalid_user(self, mock_server_a):
        """400 for order with nonexistent user."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            response = client.post("/orders", json={
                "user_id": "00000000-0000-0000-0000-000000000000",
                "items": [{"widget_id": "10000000-0000-0000-0000-000000000001", "quantity": 1}]
            })
            assert response.status_code == 400


class TestVariantDifferences:
    """Test that variant A and B produce observable differences."""

    def test_volatile_fields_differ(self, dual_servers):
        """Request IDs should always differ between calls."""
        server_a, server_b = dual_servers["a"], dual_servers["b"]

        with httpx.Client() as client:
            resp_a = client.get(f"{server_a.base_url}/health")
            resp_b = client.get(f"{server_b.base_url}/health")

            # Timestamps will differ (called at different times)
            assert resp_a.json()["timestamp"] != resp_b.json()["timestamp"]

    def test_price_tolerance_difference(self, dual_servers):
        """Variant B has slight price differences (within tolerance)."""
        server_a, server_b = dual_servers["a"], dual_servers["b"]
        widget_id = "10000000-0000-0000-0000-000000000001"

        with httpx.Client() as client:
            resp_a = client.get(f"{server_a.base_url}/widgets/{widget_id}")
            resp_b = client.get(f"{server_b.base_url}/widgets/{widget_id}")

            price_a = resp_a.json()["price"]
            price_b = resp_b.json()["price"]

            # Prices should differ (variant B adds 0.001)
            assert price_a != price_b, "Variant B should produce different prices"
            # But difference should be within tolerance
            assert abs(price_a - price_b) <= 0.01

    def test_array_order_may_differ(self, dual_servers):
        """Variant B may shuffle array order."""
        server_a, server_b = dual_servers["a"], dual_servers["b"]
        user_id = "00000000-0000-0000-0000-000000000001"

        with httpx.Client() as client:
            resp_a = client.get(f"{server_a.base_url}/users/{user_id}/profile")
            resp_b = client.get(f"{server_b.base_url}/users/{user_id}/profile")

            roles_a = resp_a.json()["roles"]
            roles_b = resp_b.json()["roles"]

            # Same elements, possibly different order
            assert set(roles_a) == set(roles_b)

    def test_score_tolerance_difference(self, dual_servers):
        """Variant B has slight score differences (within tolerance)."""
        server_a, server_b = dual_servers["a"], dual_servers["b"]
        user_id = "00000000-0000-0000-0000-000000000001"

        with httpx.Client() as client:
            resp_a = client.get(f"{server_a.base_url}/users/{user_id}/profile")
            resp_b = client.get(f"{server_b.base_url}/users/{user_id}/profile")

            scores_a = resp_a.json()["scores"]
            scores_b = resp_b.json()["scores"]

            # Scores should be close but may differ slightly
            assert abs(scores_a["reputation"] - scores_b["reputation"]) <= 0.1
            assert abs(scores_a["activity"] - scores_b["activity"]) <= 0.1
            assert abs(scores_a["trust"] - scores_b["trust"]) <= 0.01


class TestStatefulChain:
    """Test multi-step stateful operations."""

    def test_create_get_update_delete_chain(self, mock_server_a):
        """Full CRUD chain works correctly."""
        with httpx.Client(base_url=mock_server_a.base_url) as client:
            # Step 1: Create
            create_resp = client.post("/widgets", json={
                "name": "Chain Test Widget",
                "price": 49.99,
                "category": "tools",
                "tags": ["test", "chain"],
            })
            assert create_resp.status_code == 201
            widget_id = create_resp.json()["id"]

            # Step 2: Get (verify create worked)
            get_resp = client.get(f"/widgets/{widget_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["name"] == "Chain Test Widget"

            # Step 3: Update
            update_resp = client.put(f"/widgets/{widget_id}", json={
                "name": "Updated Chain Widget",
                "price": 59.99,
            })
            assert update_resp.status_code == 200
            assert update_resp.json()["name"] == "Updated Chain Widget"
            assert "updated_at" in update_resp.json()

            # Step 4: Verify update
            get_resp2 = client.get(f"/widgets/{widget_id}")
            assert get_resp2.json()["name"] == "Updated Chain Widget"

            # Step 5: Delete
            delete_resp = client.delete(f"/widgets/{widget_id}")
            assert delete_resp.status_code == 204

            # Step 6: Verify delete
            get_resp3 = client.get(f"/widgets/{widget_id}")
            assert get_resp3.status_code == 404

    def test_order_chain(self, mock_server_a):
        """Create order then retrieve it."""
        user_id = "00000000-0000-0000-0000-000000000001"
        widget_id = "10000000-0000-0000-0000-000000000001"

        with httpx.Client(base_url=mock_server_a.base_url) as client:
            # Create order
            create_resp = client.post("/orders", json={
                "user_id": user_id,
                "items": [{"widget_id": widget_id, "quantity": 3}]
            })
            assert create_resp.status_code == 201
            order_id = create_resp.json()["id"]

            # Retrieve order
            get_resp = client.get(f"/orders/{order_id}")
            assert get_resp.status_code == 200
            order = get_resp.json()
            assert order["id"] == order_id
            assert order["user_id"] == user_id
            assert len(order["items"]) == 1
            assert order["items"][0]["quantity"] == 3
