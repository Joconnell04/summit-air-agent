"""Local names are said differently only inside the voice provider (voice/names.py), so the
server's read-back sentence keeps the name exactly as written."""

from toolkit import UNKNOWN


def test_the_read_back_keeps_a_local_name_as_written(agent):
    call_id = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call_id, "Hi, the heat's out in my house.")
    address = {"street": "412 Kosciuszko St", "unit": "", "city": "Brooklyn", "zip": "11221"}
    asked = agent.tool("read_back", call_id, address=address, phone="caller_id")
    assert asked["ok"], asked
    assert "412 Kosciuszko St" in asked["say"]
    assert "<<" not in asked["say"] and "koss" not in asked["say"].lower()
