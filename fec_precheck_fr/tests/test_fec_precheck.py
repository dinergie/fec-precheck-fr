# Part of FEC Pre-Export Check. See LICENSE file for full copyright and licensing details.
from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.fec_precheck_fr.models import rules
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestFecPrecheck(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country('fr')
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company.vat = 'FR40303265045'
        cls.partner = cls.env['res.partner'].create({'name': 'Client Test', 'is_company': True, 'country_id': cls.env.ref('base.fr').id})
        cls.payable = cls.company_data['default_account_payable']
        cls.expense = cls.company_data['default_account_expense']

    def _run(self):
        run = self.env['fec.precheck.run'].create({'company_id': self.company.id, 'date_from': '2025-01-01', 'date_to': '2025-12-31'})
        run.action_run()
        return run, {finding.code: finding for finding in run.finding_ids}

    def _entry(self, lines, **values):
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'date': '2025-03-01',
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [Command.create(line) for line in lines],
            **values,
        })
        move.action_post()
        return move

    def test_rules_account_numbers(self):
        self.assertTrue(rules.in_pcg('401QINERA'))
        self.assertTrue(rules.in_pcg('445710'))
        self.assertTrue(rules.in_pcg('700000'))
        self.assertFalse(rules.in_pcg('999999'))
        self.assertFalse(rules.in_pcg('BNK1'))

    def test_rules_siren(self):
        self.assertTrue(rules.siren_valid('303265045'))
        self.assertFalse(rules.siren_valid('303265046'))
        self.assertFalse(rules.siren_valid('12345'))

    def test_missing_partner_reference_label(self):
        move = self._entry([
            {'account_id': self.expense.id, 'name': False, 'debit': 50},
            {'account_id': self.payable.id, 'name': 'Debt', 'credit': 50},
        ])
        run, found = self._run()
        self.assertEqual(run.state, 'done')
        self.assertEqual(found['L_AUX_MISSING'].count, 1)
        self.assertEqual(found['L_AUX_MISSING'].amount, 50)
        self.assertEqual(found['L_REF_MISSING'].sample_ids[0].res_id, move.id)
        self.assertIn('L_LABEL_MISSING', found)

    def test_draft_and_partner_reference(self):
        invoice = self.init_invoice('out_invoice', partner=self.partner, invoice_date='2025-04-01', amounts=[100], post=True)
        self.init_invoice('out_invoice', partner=self.partner, invoice_date='2025-04-02', amounts=[100], post=False)
        run, found = self._run()
        self.assertEqual(found['B_DRAFT_INVOICES'].count, 1)
        self.assertEqual(found['T_NO_REF'].sample_ids[0].res_id, self.partner.id)
        self.assertEqual(run.line_count, len(invoice.line_ids.filtered(lambda line: line.display_type not in ('line_section', 'line_note'))))

    def test_empty_period(self):
        run, found = self._run()
        self.assertEqual(run.line_count, 0)
        self.assertIn('L_EMPTY', found)

    def test_check_changes_nothing(self):
        """The check must not modify any accounting record."""
        invoice = self.init_invoice('out_invoice', partner=self.partner, invoice_date='2025-04-01', amounts=[100], post=True)
        self.env.flush_all()
        before = invoice.write_date, self.partner.write_date, invoice.line_ids.mapped('write_date'), self.company.write_date
        ref = invoice.ref
        self._run()
        self.env.flush_all()
        self.assertEqual(before, (invoice.write_date, self.partner.write_date, invoice.line_ids.mapped('write_date'), self.company.write_date))
        self.assertEqual(invoice.ref, ref)
        self.assertEqual(self.company.fiscalyear_lock_date, False)

    def test_read_only_user(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Reader', 'login': 'fec_reader', 'company_id': self.company.id, 'company_ids': [Command.set([self.company.id])],
            ('group_ids' if 'group_ids' in self.env['res.users']._fields else 'groups_id'): [Command.set([self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id])],
        })
        run = self.env['fec.precheck.run'].with_user(user).create({'company_id': self.company.id, 'date_from': '2025-01-01', 'date_to': '2025-12-31'})
        run.action_run()
        self.assertEqual(run.state, 'done')

    def test_second_run_replaces_findings(self):
        self._entry([
            {'account_id': self.expense.id, 'name': 'Cost', 'debit': 50},
            {'account_id': self.payable.id, 'name': 'Debt', 'credit': 50},
        ])
        run, found = self._run()
        first = len(run.finding_ids)
        run.action_run()
        self.assertEqual(len(run.finding_ids), first)
