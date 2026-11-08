async def _create(client, headers, **kw):
    body = {"display_name": "Алексей", **kw}
    r = await client.post("/api/v1/persons", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_roundtrip(client, alice):
    p = await _create(client, alice["headers"], category="friend", telegram_username="@alex_01", languages=["ru"])
    pid = p["id"]
    r = await client.patch(f"/api/v1/persons/{pid}", json={"city": "Almaty", "notes": "met at conf"}, headers=alice["headers"])
    assert r.status_code == 200 and r.json()["city"] == "Almaty"
    assert (await client.get(f"/api/v1/persons/{pid}", headers=alice["headers"])).json()["notes"] == "met at conf"
    assert (await client.delete(f"/api/v1/persons/{pid}", headers=alice["headers"])).status_code == 204
    assert (await client.get(f"/api/v1/persons/{pid}", headers=alice["headers"])).status_code == 404


async def test_list_filter_sort_paginate(client, alice):
    for name, cat in [("Charlie", "work"), ("Anna", "friend"), ("Boris", "friend")]:
        await _create(client, alice["headers"], display_name=name, category=cat)
    r = await client.get("/api/v1/persons?category=friend&sort=display_name&limit=1&offset=1", headers=alice["headers"])
    body = r.json()
    assert body["total"] == 2 and [i["display_name"] for i in body["items"]] == ["Boris"]
    r = await client.get("/api/v1/persons?q=char", headers=alice["headers"])
    assert [i["display_name"] for i in r.json()["items"]] == ["Charlie"]


async def test_duplicate_username_conflict(client, alice):
    await _create(client, alice["headers"], telegram_username="dup_user")
    r = await client.post("/api/v1/persons", json={"display_name": "X", "telegram_username": "@DUP_user"}, headers=alice["headers"])
    assert r.status_code == 409


async def test_validation(client, alice):
    h = alice["headers"]
    assert (await client.post("/api/v1/persons", json={"display_name": ""}, headers=h)).status_code == 422
    assert (await client.post("/api/v1/persons", json={"display_name": "A", "birth_year": 1500}, headers=h)).status_code == 422
    assert (await client.post("/api/v1/persons", json={"display_name": "A", "owner_id": "x"}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/persons?limit=1000", headers=h)).status_code == 422
    assert (await client.get("/api/v1/persons/not-a-uuid", headers=h)).status_code == 422
