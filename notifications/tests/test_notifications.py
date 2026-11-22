from fastapi.testclient import TestClient

from app import main


def test_renders_each_event_once():
    main.seen.clear()
    event = {"order_id": "abcdef123456", "customer": "ana@example.com", "total": 82000}
    email = main.render("order.confirmed", event)
    assert email["to"] == "ana@example.com"
    assert "82,000" in email["body"] and "abcdef12" in email["body"]
    assert main.render("order.confirmed", event) is None  # duplicate delivery


def test_rejected_email_explains_why():
    main.seen.clear()
    email = main.render("order.rejected", {"order_id": "x1", "customer": "b@x.com", "total": 1})
    assert "ran out of stock" in email["body"]


def test_api_filters_by_recipient():
    main.sent[:] = [{"to": "a@x.com"}, {"to": "b@x.com"}]
    client = TestClient(main.app)
    assert client.get("/notifications", params={"to": "b@x.com"}).json() == [{"to": "b@x.com"}]


def test_duplicates_are_counted():
    main.seen.clear()
    before = main.DUPLICATES._value.get()
    event = {"order_id": "dup1", "customer": "a@x.com", "total": 1}
    main.render("order.confirmed", event)
    main.render("order.confirmed", event)
    assert main.DUPLICATES._value.get() == before + 1
    assert "shop_notifications_duplicates_total" in TestClient(main.app).get("/metrics/").text
