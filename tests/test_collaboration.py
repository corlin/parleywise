import unittest
import review


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, model, system, user):
        self.calls.append((model, system, user))
        if system == review.REVIEW_SYS:
            self.review_input = user
            return [{'rule_id': 'R01', 'quote': '保证收益', 'reason': '承诺收益'}]
        if system == review.TRIAGE_SYS:
            return [{'rule_id': 'R01', 'quote': '保证收益'}]
        return [{'rule_id': 'R02', 'quote': '不存在的引用'}]


class CollaborationTests(unittest.TestCase):
    def test_independent_proposals_are_combined_for_review_then_verified(self):
        rules = {
            'R01': {'id': 'R01', 'title': '收益', 'description': '不得保证收益', 'check': 'llm', 'applies_to': 'public'},
            'R02': {'id': 'R02', 'title': '风险', 'description': '提示风险', 'check': 'llm', 'applies_to': 'public'},
        }
        llm = FakeLLM()
        kept, _ = review.review_doc('保证收益', 'public', rules, llm, 'cheap', 'strong', collaboration=True)
        self.assertEqual([f['rule_id'] for f in kept], ['R01'])
        self.assertIn('R01', llm.review_input)
        self.assertIn('R02', llm.review_input)
        self.assertEqual(len(llm.calls), 3)
        triage_inputs = [user for _, system, user in llm.calls if system != review.REVIEW_SYS]
        self.assertEqual(triage_inputs[0], triage_inputs[1])

class CollaborationTraceTests(unittest.TestCase):
    def test_trace_records_each_role_and_review_without_changing_result(self):
        rules = {'R01': {'id': 'R01', 'title': '收益', 'description': '不得保证收益', 'check': 'llm', 'applies_to': 'public'},
                 'R02': {'id': 'R02', 'title': '风险', 'description': '提示风险', 'check': 'llm', 'applies_to': 'public'}}
        events = []
        kept, _ = review.review_doc('保证收益', 'public', rules, FakeLLM(), 'cheap', 'strong',
                                    collaboration=True, on_collaboration=events.append)
        self.assertEqual([event['role'] for event in events], ['triage', 'independent_triage', 'review'])
        self.assertEqual(events[0]['findings'][0]['rule_id'], 'R01')
        self.assertEqual(events[1]['findings'][0]['rule_id'], 'R02')
        self.assertEqual(kept[0]['rule_id'], 'R01')


if __name__ == '__main__':
    unittest.main()
