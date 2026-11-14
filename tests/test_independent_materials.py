import unittest
from evaluation.independent_materials import select_windows,POLICY


class IndependentMaterialTests(unittest.TestCase):
    def test_systematic_selection_is_deterministic_and_exactly_source_bound(self):
        text='\n\n'.join('第'+str(i)+'段：'+('传感器记录环境温度，读数保留来源与时间。'*25) for i in range(30))
        selected=select_windows(text)
        self.assertEqual(len(selected),12);self.assertEqual(selected,select_windows(text))
        self.assertEqual(len({w['start'] for w in selected}),12)
        for window in selected:self.assertEqual(text[window['start']:window['end']],window['quote'])
        self.assertFalse(POLICY['replacement_after_results'])
        self.assertFalse(POLICY['live_execution_enabled'])

    def test_insufficient_material_cannot_silently_reduce_the_frozen_population(self):
        with self.assertRaises(ValueError):select_windows('Only a short passage.')
