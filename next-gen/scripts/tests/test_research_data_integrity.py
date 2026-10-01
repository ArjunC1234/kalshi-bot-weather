import unittest
from unittest.mock import Mock

from libs.config import SupabaseConfig
from libs.supabase_client import SupabaseClient
from scripts.repair_research_quotes import quantity


class ResearchDataIntegrityTests(unittest.TestCase):
    def test_pagination_orders_by_primary_key(self):
        client = SupabaseClient(SupabaseConfig("https://example.invalid", "not-a-key"))
        client.page_size = 2
        client.session = Mock()
        responses = []
        for rows in ([{"event_id": "a"}, {"event_id": "b"}], [{"event_id": "c"}]):
            response = Mock(status_code=200)
            response.json.return_value = rows
            responses.append(response)
        client.session.get.side_effect = responses
        result = client.select("events", {"select": "event_id"})
        self.assertEqual([r["event_id"] for r in result], ["a", "b", "c"])
        for call in client.session.get.call_args_list:
            self.assertEqual(call.kwargs["params"]["order"], "event_id.asc")
        self.assertEqual(client.session.get.call_args_list[1].kwargs["headers"]["Range"], "2-3")

    def test_explicit_order_is_preserved(self):
        client = SupabaseClient(SupabaseConfig("https://example.invalid", "not-a-key"))
        client.session = Mock()
        client.session.get.return_value.status_code = 200
        client.session.get.return_value.json.return_value = []
        client.select("settlements", {"order": "target_date.desc,settlement_id.asc"})
        self.assertEqual(client.session.get.call_args.kwargs["params"]["order"],
                         "target_date.desc,settlement_id.asc")

    def test_raw_no_ask_quantity_comes_from_yes_bid(self):
        self.assertEqual(quantity({"yes_bid_size_fp": "17.25"}, "no", "ask"), 17.25)
        self.assertEqual(quantity({"yes_ask_size_fp": "44.76"}, "no", "bid"), 44.76)
        self.assertIsNone(quantity({}, "no", "ask"))


if __name__ == "__main__":
    unittest.main()
