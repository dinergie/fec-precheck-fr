# Part of FEC Check (Morocco). See LICENSE file for full copyright and licensing details.
import datetime

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.fec_check_ma.models import rules
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestFecCheck(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country('ma')
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company.write({'company_registry': '001234567000089', 'vat': '12345678'})
        cls.partner = cls.env['res.partner'].create({'name': 'Client Test', 'is_company': True, 'country_id': cls.env.ref('base.ma').id})
        cls.payable = cls.company_data['default_account_payable']
        cls.expense = cls.company_data['default_account_expense']

    def _run(self):
        run = self.env['fec.check.ma.run'].create({'company_id': self.company.id, 'date_from': '2025-01-01', 'date_to': '2025-12-31'})
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
        self.assertTrue(rules.in_cgnc('441100'))
        self.assertTrue(rules.in_cgnc('3421QINERA'))
        self.assertTrue(rules.in_cgnc('711100'))
        self.assertTrue(rules.in_cgnc('8110000000'))
        self.assertFalse(rules.in_cgnc('999999'))
        self.assertFalse(rules.in_cgnc('401000'))
        self.assertFalse(rules.in_cgnc('BNK1'))

    def test_rules_ice(self):
        self.assertTrue(rules.ice_valid('001234567000089'))
        self.assertFalse(rules.ice_valid('000000000000000'))
        self.assertFalse(rules.ice_valid('12345'))
        self.assertFalse(rules.ice_valid(False))

    def test_rules_open_periods(self):
        self.assertEqual(rules.open_limit(datetime.date(2026, 3, 15)), datetime.date(2026, 1, 31))
        self.assertEqual(rules.open_limit(datetime.date(2026, 1, 1)), datetime.date(2025, 11, 30))

    def test_rules_settings(self):
        words = {name: name for name in ('empty', 'Suppliers','Customers', 'Recoverable VAT', 'Invoiced VAT', 'Banks', 'Sales', 'expense', 'income', 'receivable', 'payable', 'not_receivable', 'not_payable', 'no_account')}
        company = {'id': 1, 'name': 'Test', 'ice': '', 'if': '', 'country_code': 'MA', 'chart_template': 'fr'}
        accounts = {
            1: {'code': '611100', 'name': 'Wrong', 'type': 'income'},
            2: {'code': '342100', 'name': 'Wrong', 'type': 'asset_current'},
            3: {'code': '445500', 'name': 'Tax', 'type': 'liability_current'},
        }
        journals = {1: {'name': 'Bank', 'code': 'BNK', 'type': 'bank', 'account_code': '512000'}, 2: {'name': 'Cash', 'code': 'CSH', 'type': 'cash', 'account_code': '514100'}}
        taxes = [
            {'id': 1, 'name': 'Sale', 'amount': 20, 'use': 'sale', 'accounts': [('345520', 100)]},
            {'id': 2, 'name': 'Purchase', 'amount': 20, 'use': 'purchase', 'accounts': [('445500', 100)]},
            {'id': 3, 'name': 'Reverse', 'amount': 20, 'use': 'purchase', 'accounts': [('345520', 100), ('445500', -100)]},
            {'id': 4, 'name': 'No account', 'amount': 20, 'use': 'sale', 'accounts': [('', 100)]},
        ]
        results = rules.Results()
        rules.check_settings(results, company, accounts, journals, taxes, words)
        self.assertEqual(results.count('C_COMPANY_ICE'), 1)
        self.assertEqual(results.count('C_COMPANY_IF'), 1)
        self.assertEqual(results.count('C_CHART_NOT_MA'), 1)
        self.assertEqual(results.count('C_ACCOUNT_TYPE'), 2)
        self.assertEqual(results.count('C_TAX_ACCOUNT'), 3)
        self.assertEqual(results.count('C_JOURNAL_BANK'), 2)
        self.assertEqual(results.count('C_KEY_ACCOUNT_MISSING'), 4)

    def test_rules_entry(self):
        accounts = {1: {'code': '711100', 'name': 'Sale', 'type': 'income'}, 2: {'code': '342100', 'name': 'Customer', 'type': 'asset_receivable'}}
        move = {'id': 1, 'name': '', 'ref': '', 'date': '2025-03-01', 'invoice_date': '2025-03-20', 'move_type': 'out_invoice', 'journal_type': 'sale', 'journal_code': 'VTE', 'gap': False, 'cash_basis': False}
        line = {'account_id': 1, 'partner_id': 1, 'name': 'Sale', 'debit': 0, 'credit': 100, 'display_type': 'product', 'tax_ids': []}
        results = rules.Results()
        rules.check_entry(results, move, [line], accounts, {})
        self.assertEqual(results.count('L_UNBALANCED'), 1)
        self.assertEqual(results.count('L_NUM_MISSING'), 1)
        self.assertEqual(results.count('L_PIECE_DATE'), 1)
        self.assertEqual(results.count('B_NO_TAX'), 1)
        rules.finalize(results, 0, 1, {})
        self.assertEqual(results.count('B_NO_TAX'), 0)

    def test_chart_of_odoo_is_clean(self):
        """A Moroccan company on the chart of Odoo must not be reported for its settings."""
        run, found = self._run()
        settings = {code for code in found if code.startswith('C_') and code not in ('C_OPEN_PERIODS', 'C_YEAR_NOT_CLOSED')}
        self.assertFalse(settings, [(found[code].title, found[code].sample_ids.mapped('label')) for code in settings])

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

    def test_customer_invoice_number_is_its_reference(self):
        self.init_invoice('out_invoice', partner=self.partner, invoice_date='2025-04-01', amounts=[100], post=True)
        run, found = self._run()
        self.assertNotIn('L_REF_MISSING', found)
        self.assertEqual(found['T_NO_ICE'].sample_ids[0].res_id, self.partner.id)
        self.assertEqual(found['T_NO_REF'].sample_ids[0].res_id, self.partner.id)

    def test_vat_settlement_needs_no_partner(self):
        """Odoo types 4456 as payable: a VAT return line is not a supplier line."""
        vat_due = self.env['account.account'].search([*self.env['account.account']._check_company_domain(self.company), ('code', '=like', '4456%')], limit=1)
        self.assertEqual(vat_due.account_type, 'liability_payable')
        self._entry([
            {'account_id': self.expense.id, 'name': 'Cost', 'debit': 50},
            {'account_id': vat_due.id, 'name': 'VAT due', 'credit': 50},
        ], ref='TVA-01')
        run, found = self._run()
        self.assertNotIn('L_AUX_MISSING', found)

    def test_chronology(self):
        journal = self.company_data['default_journal_misc']
        lines = [
            {'account_id': self.expense.id, 'name': 'Cost', 'debit': 50},
            {'account_id': self.payable.id, 'name': 'Debt', 'partner_id': self.partner.id, 'credit': 50},
        ]
        first = self._entry(lines, ref='A', date='2025-03-10', journal_id=journal.id)
        second = self._entry(lines, ref='B', date='2025-03-02', journal_id=journal.id)
        self.assertEqual(first.sequence_prefix, second.sequence_prefix)
        run, found = self._run()
        self.assertEqual(found['L_CHRONOLOGY'].sample_ids[0].res_id, second.id)

    def test_periods(self):
        self.company.fiscalyear_lock_date = False
        self._entry([
            {'account_id': self.expense.id, 'name': 'Cost', 'debit': 50},
            {'account_id': self.payable.id, 'name': 'Debt', 'partner_id': self.partner.id, 'credit': 50},
        ], ref='A')
        run, found = self._run()
        self.assertIn('C_OPEN_PERIODS', found)
        self.assertIn('C_YEAR_NOT_CLOSED', found)

    def test_empty_period(self):
        run, found = self._run()
        self.assertEqual(run.line_count, 0)
        self.assertIn('L_EMPTY', found)
        self.assertNotIn('C_OPEN_PERIODS', found)

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
            'name': 'Reader', 'login': 'fec_reader_ma', 'company_id': self.company.id, 'company_ids': [Command.set([self.company.id])],
            ('group_ids' if 'group_ids' in self.env['res.users']._fields else 'groups_id'): [Command.set([self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id])],
        })
        run = self.env['fec.check.ma.run'].with_user(user).create({'company_id': self.company.id, 'date_from': '2025-01-01', 'date_to': '2025-12-31'})
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
