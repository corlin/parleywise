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


class KeywordReviewTests(unittest.TestCase):
    def test_keyword_hits_go_through_strong_review_when_online(self):
        class RejectAllReviewLLM:
            def __init__(self):
                self.review_inputs = []

            def chat(self, model, system, user):
                if system == review.REVIEW_SYS:
                    self.review_inputs.append(user)
                    return []
                return []

        rules = {
            'R19': {
                'id': 'R19',
                'title': '使投资者忽视风险的用语',
                'description': '不得使用安全等表述',
                'check': 'keyword',
                'keywords': ['安全'],
                'applies_to': 'public',
            }
        }
        text = '任何宣称“安全”的说法均不符合监管规定。'
        llm = RejectAllReviewLLM()
        kept, dropped = review.review_doc(text, 'public', rules, llm, 'cheap', 'strong')
        self.assertEqual(kept, [])
        self.assertEqual(len(llm.review_inputs), 1)
        self.assertIn('R19', llm.review_inputs[0])
        self.assertEqual([d['rule_id'] for d in dropped], ['R19'])

        kept_offline, _ = review.review_doc(text, 'public', rules, None)
        self.assertEqual([f['rule_id'] for f in kept_offline], ['R19'])


if __name__ == '__main__':
    unittest.main()
