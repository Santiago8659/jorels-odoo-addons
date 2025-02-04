# -*- coding: utf-8 -*-
#
# Jorels S.A.S. - Copyright (2019-2022)
#
# This file is part of l10n_co_edi_jorels.
#
# l10n_co_edi_jorels is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# l10n_co_edi_jorels is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with l10n_co_edi_jorels.  If not, see <https://www.gnu.org/licenses/>.
#
# email: info@jorels.com
#

import base64
import json
import logging
import math
import re
from io import BytesIO

import qrcode
import requests
from num2words import num2words
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountInvoice(models.Model):
    _inherit = "account.invoice"
    _description = "Electronic invoicing"

    state = fields.Selection(selection_add=[('validate', 'Validating DIAN')])
    number_formatted = fields.Char(string="Number formatted", compute="compute_number_formatted", store=True,
                                   copy=False)

    ei_type_document_id = fields.Many2one(comodel_name='l10n_co_edi_jorels.type_documents', string="Document type",
                                          copy=False, ondelete='RESTRICT')
    ei_customer = fields.Text(string="customer json", copy=False)
    ei_legal_monetary_totals = fields.Text(string="legal_monetary_totals json", copy=False)
    ei_invoice_lines = fields.Text(string="invoice_lines json", copy=False)

    # They allow to store synchronous and production modes used when invoicing
    ei_sync = fields.Boolean(string="Sync", default=False, copy=False, readonly=True)
    ei_is_not_test = fields.Boolean(string="In production", copy=False, readonly=True,
                                    default=lambda self: self.env['res.company']._company_default_get().is_not_test,
                                    store=True, compute="_compute_ei_is_not_test")

    # API Response:
    ei_is_valid = fields.Boolean(string="Valid", copy=False, readonly=True, states={'draft': [('readonly', False)]})
    ei_is_restored = fields.Boolean("Is restored?", copy=False, readonly=True)
    ei_algorithm = fields.Char(string="Algorithm", copy=False, readonly=True)
    ei_class = fields.Char("Class", copy=False, readonly=True)
    ei_number = fields.Char(string="Number", compute="compute_number_formatted", store=True, copy=False, readonly=True)
    ei_uuid = fields.Char(string="UUID", copy=False, readonly=True, states={'draft': [('readonly', False)]})
    ei_issue_date = fields.Date(string="Issue date", copy=False, readonly=True,
                                states={'draft': [('readonly', False)]})
    ei_expedition_date = fields.Char("Expedition date", copy=False, readonly=True)
    ei_zip_key = fields.Char(string="Zip key", copy=False, readonly=True, states={'draft': [('readonly', False)]})
    ei_status_code = fields.Char(string="Status code", copy=False, readonly=True)
    ei_status_description = fields.Char(string="Status description", copy=False, readonly=True)
    ei_status_message = fields.Char(string="Status message", copy=False, readonly=True)
    ei_errors_messages = fields.Text("Message", copy=False, readonly=True)
    ei_xml_name = fields.Char(string="Xml name", copy=False, readonly=True)
    ei_zip_name = fields.Char(string="Zip name", copy=False, readonly=True)
    ei_signature = fields.Char(string="Signature", copy=False, readonly=True)
    ei_qr_code = fields.Char("QR code", copy=False, readonly=True)
    ei_qr_data = fields.Text(string="QR data", copy=False, readonly=True)
    ei_qr_link = fields.Char("QR link", copy=False, readonly=True)
    ei_pdf_download_link = fields.Char("PDF link", copy=False, readonly=True)
    ei_xml_base64_bytes = fields.Binary('XML', attachment=True, copy=False, readonly=True)
    ei_application_response_base64_bytes = fields.Binary("Application response", attachment=True, copy=False,
                                                         readonly=True)
    ei_attached_document_base64_bytes = fields.Binary("Attached document", attachment=True, copy=False, readonly=True,
                                                      states={'draft': [('readonly', False)]})
    ei_pdf_base64_bytes = fields.Binary('Pdf document', attachment=True, copy=False, readonly=True,
                                        states={'draft': [('readonly', False)]})
    ei_zip_base64_bytes = fields.Binary('Zip document', attachment=True, copy=False, readonly=True)
    ei_type_environment = fields.Many2one(comodel_name="l10n_co_edi_jorels.type_environments",
                                          string="Type environment", copy=False, readonly=True,
                                          states={'draft': [('readonly', False)]},
                                          default=lambda self: self._default_ei_type_environment())
    ei_payload = fields.Text("Payload", copy=False, readonly=True)

    # Old fields, compatibility
    ei_xml_file_name = fields.Char(string="Xml file name", copy=False, readonly=True)
    ei_url_acceptance = fields.Char(string="URL acceptance", copy=False, readonly=True)
    ei_url_rejection = fields.Char(string="URL rejection", copy=False, readonly=True)
    ei_xml_bytes = fields.Boolean(string="XML Bytes", copy=False, readonly=True)
    ei_dian_response_base64_bytes = fields.Binary('DIAN response', attachment=True, copy=False, readonly=True,
                                                  states={'draft': [('readonly', False)]})

    # For mail attached
    ei_attached_zip_base64_bytes = fields.Binary('Attached zip', attachment=True, copy=False, readonly=True,
                                                 states={'draft': [('readonly', False)]})

    # QR image
    ei_qr_image = fields.Binary("QR image", attachment=True, copy=False, readonly=True)

    # Total taxes only / without withholdings
    ei_amount_tax_withholding = fields.Monetary("Withholdings", compute="_compute_amount", store=True)
    ei_amount_tax_no_withholding = fields.Monetary("Taxes without withholdings", compute="_compute_amount", store=True)
    ei_amount_total_no_withholding = fields.Monetary("Total without withholdings", compute="_compute_amount",
                                                     store=True)

    # Total base excluding tax
    ei_amount_excluded = fields.Monetary("Excluded", compute="_compute_amount", store=True)

    # Required field for credit and debit notes in DIAN
    ei_correction_concept_id = fields.Many2one(comodel_name='l10n_co_edi_jorels.correction_concepts',
                                               string="Correction concept", copy=False, readonly=True,
                                               compute="compute_ei_correction_concept_id", store=True,
                                               ondelete='RESTRICT')
    ei_correction_concept_credit_id = fields.Many2one(comodel_name='l10n_co_edi_jorels.correction_concepts',
                                                      string="Credit correction concept", copy=False,
                                                      readonly=True,
                                                      domain=[('type_document_id', '=', '5')], ondelete='RESTRICT',
                                                      states={'draft': [('readonly', False)]})
    ei_correction_concept_debit_id = fields.Many2one(comodel_name='l10n_co_edi_jorels.correction_concepts',
                                                     string="Debit correction concept", copy=False, readonly=True,
                                                     domain=[('type_document_id', '=', '6')], ondelete='RESTRICT',
                                                     states={'draft': [('readonly', False)]})
    ei_is_correction_without_reference = fields.Boolean("Is it a correction without reference?", default=False,
                                                        readonly=True, states={'draft': [('readonly', False)]})

    value_letters = fields.Char("Value in letters", compute="_compute_amount", store=True)

    is_attached_document_matched = fields.Boolean("Correct number in attached document?", copy=False,
                                                  compute='_is_attached_document_matched', store=True)
    ei_operation = fields.Selection([
        ('aiu', 'AIU'),
        ('standard', 'Standard'),
        ('mandates', 'Mandates'),
        ('transport', 'Transport'),
        ('exchange', 'Exchange')
    ], string="Operation type", default=lambda self: self.env.user.company_id.ei_operation, copy=True, readonly=True,
        required=True, states={'draft': [('readonly', False)]})

    # DIAN events
    event = fields.Selection([
        ('none', 'None'),
        ('receipt', 'Acknowledgment of receipt'),
        ('rejection', 'Document Rejection'),
        ('acceptance', 'Express acceptance of document'),
    ], string="Event", default='none', copy=False, readonly=True, required=True)

    # Period
    date_start = fields.Date(string="Start date", default=None, copy=True, readonly=True,
                             states={'draft': [('readonly', False)]})
    date_end = fields.Date(string="End date", default=None, copy=True, readonly=True,
                           states={'draft': [('readonly', False)]})

    # Order Reference
    order_ref_number = fields.Char(string="Order reference", default=None, copy=False, readonly=True,
                                   states={'draft': [('readonly', False)]})
    order_ref_date = fields.Date(string="Order date", default=None, copy=False, readonly=True,
                                 states={'draft': [('readonly', False)]})

    # Is out of country
    is_out_country = fields.Boolean(string='Is it for out of the country?',
                                    default=lambda self: self.get_default_is_out_country(),
                                    readonly=True, states={'draft': [('readonly', False)]})

    # Payment form
    payment_form_id = fields.Many2one(string="Payment form", comodel_name='l10n_co_edi_jorels.payment_forms',
                                      copy=True, store=True, compute="_compute_payment_form_id",
                                      readonly=True, ondelete='RESTRICT')
    payment_method_id = fields.Many2one(string="Payment method", comodel_name='l10n_co_edi_jorels.payment_methods',
                                        default=lambda self: self._default_payment_method_id(), copy=True,
                                        readonly=True, states={'draft': [('readonly', False)]},
                                        domain=[('scope', '=', '')], ondelete='RESTRICT')

    # Store resolution
    resolution_id = fields.Many2one(string="Resolution", comodel_name='l10n_co_edi_jorels.resolution', copy=False,
                                    store=True, compute="_compute_resolution", ondelete='RESTRICT')

    radian_ids = fields.One2many(comodel_name='l10n_co_edi_jorels.radian', inverse_name='invoice_id')

    @api.multi
    def _default_ei_type_environment(self):
        if not self.env['l10n_co_edi_jorels.type_environments'].search_count([]):
            self.env['res.company'].init_csv_data('l10n_co_edi_jorels.l10n_co_edi_jorels.type_environments')
        return 1 if self.env['res.company']._company_default_get().is_not_test else 2

    @api.depends("ei_type_environment")
    def _compute_ei_is_not_test(self):
        for rec in self:
            if rec.ei_type_environment:
                rec.ei_is_not_test = (rec.ei_type_environment.id == 1)
            else:
                rec.ei_is_not_test = rec.company_id.is_not_test

    @api.multi
    def _default_payment_method_id(self):
        if not self.env['l10n_co_edi_jorels.payment_methods'].search_count([]):
            self.env['res.company'].init_csv_data('l10n_co_edi_jorels.l10n_co_edi_jorels.payment_methods')
        return 1

    @api.depends('date_invoice', 'date_due')
    def _compute_payment_form_id(self):
        if not self.env['l10n_co_edi_jorels.payment_forms'].search_count([]):
            self.env['res.company'].init_csv_data('l10n_co_edi_jorels.l10n_co_edi_jorels.payment_forms')

        for rec in self:
            if rec.date_invoice and rec.date_due:
                if rec.date_invoice >= rec.date_due:
                    # Cash
                    rec.payment_form_id = 1
                else:
                    # Credit
                    rec.payment_form_id = 2
            else:
                # Cash
                rec.payment_form_id = 1

    @api.multi
    def get_default_is_out_country(self):
        for rec in self:
            return bool(rec.journal_id.is_out_country)

    @api.onchange('journal_id')
    def _onchange_is_out_country(self):
        for rec in self:
            rec.is_out_country = rec.get_default_is_out_country()

    @api.multi
    def is_journal_pos(self):
        self.ensure_one()
        try:
            journal_pos_rec = self.env['pos.config'].search([('invoice_journal_id.id', '=', self.journal_id.id)])
            return bool(journal_pos_rec)
        except KeyError:
            return False

    @api.model
    def is_universal_discount(self):
        try:
            if 'ks_amount_discount' in self:
                return bool(self.env['res.company'].search([('ks_enable_discount', '=', True)]))
            else:
                return False
        except KeyError:
            return False

    @api.multi
    def write_response(self, response, payload):
        try:
            for rec in self:
                rec.ei_is_valid = response['is_valid']
                rec.ei_is_restored = response['is_restored']
                rec.ei_algorithm = response['algorithm']
                rec.ei_class = response['class']
                rec.ei_number = response['number']
                rec.ei_uuid = response['uuid']
                rec.ei_issue_date = response['issue_date']
                rec.ei_expedition_date = response['expedition_date']
                rec.ei_zip_key = response['zip_key']
                rec.ei_status_code = response['status_code']
                rec.ei_status_description = response['status_description']
                rec.ei_status_message = response['status_message']
                rec.ei_errors_messages = str(response['errors_messages'])
                rec.ei_xml_name = response['xml_name']
                rec.ei_zip_name = response['zip_name']
                rec.ei_signature = response['signature']
                rec.ei_qr_code = response['qr_code']
                rec.ei_qr_data = response['qr_data']
                rec.ei_qr_link = response['qr_link']
                rec.ei_pdf_download_link = response['pdf_download_link']
                rec.ei_xml_base64_bytes = response['xml_base64_bytes']
                rec.ei_application_response_base64_bytes = response['application_response_base64_bytes']
                rec.ei_attached_document_base64_bytes = response['attached_document_base64_bytes']
                rec.ei_pdf_base64_bytes = response['pdf_base64_bytes']
                rec.ei_zip_base64_bytes = response['zip_base64_bytes']
                rec.ei_type_environment = response['type_environment_id']
                rec.ei_payload = payload

                # QR code
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=2,
                    border=2,
                )
                qr.add_data(rec.ei_qr_data)
                qr.make(fit=True)
                img = qr.make_image()
                temp = BytesIO()
                img.save(temp, format="PNG")
                qr_image = base64.b64encode(temp.getvalue())
                rec.ei_qr_image = qr_image
        except Exception as e:
            _logger.debug("Write response: %s", e)

    @api.multi
    def get_type_document_identification_id(self):
        for rec in self:
            if rec.partner_id.type == 'invoice' and rec.partner_id.parent_id:
                rec_partner = rec.partner_id.parent_id
            else:
                rec_partner = rec.partner_id

            return rec_partner.type_document_identification_id.id

    @api.multi
    def get_ei_customer(self):
        for rec in self:
            if rec.partner_id.type == 'invoice' and rec.partner_id.parent_id:
                rec_partner = rec.partner_id.parent_id

                if rec.partner_id.email:
                    email_edi = rec.partner_id.email
                else:
                    raise UserError(_("The client must have an email where to send the invoice."))
            else:
                rec_partner = rec.partner_id

                if rec_partner.email_edi:
                    email_edi = rec_partner.email_edi
                else:
                    raise UserError(_("The client must have an email where to send the invoice."))

            type_document_identification_id = rec.get_type_document_identification_id()
            if type_document_identification_id:
                if rec.partner_id.vat:
                    identification_number_general = ''.join([i for i in rec.partner_id.vat if i.isdigit()])

                    # If it is Nit remove the check digit
                    if type_document_identification_id == 6:
                        identification_number = identification_number_general[:-1]
                    else:
                        identification_number = identification_number_general

                    if identification_number:
                        name = rec_partner.name

                        type_organization_id = 1 if rec_partner.is_company else 2

                        if rec_partner.merchant_registration:
                            merchant_registration = rec_partner.merchant_registration
                        else:
                            merchant_registration = 'No tiene'

                        customer_data = {
                            "id_code": type_document_identification_id,
                            "id_number": identification_number,
                            "organization_code": type_organization_id,
                            "name": name,
                            "email": email_edi,
                            "merchant_registration": merchant_registration
                        }

                        if rec_partner.trade_name:
                            customer_data['trade_name'] = rec_partner.trade_name

                        if rec_partner.country_id:
                            customer_rec = self.env['l10n_co_edi_jorels.countries'].search(
                                [('code', '=', rec_partner.country_id.code)]
                            )
                            if customer_rec:
                                customer_data['country_code'] = customer_rec.id
                            else:
                                raise UserError(_("You must assign the client a valid country"))
                        else:
                            raise UserError(_("You must assign the client a country"))

                        if rec_partner.country_id.code == 'CO' and rec.is_out_country:
                            raise UserError(_("This is an export invoice but the client's country is Colombia"))

                        if rec_partner.municipality_id and rec_partner.country_id.code == 'CO':
                            customer_data['municipality_code'] = rec_partner.municipality_id.id
                        elif rec_partner.country_id.code == 'CO':
                            raise UserError(_("You must assign the client a municipality"))

                        if rec_partner.type_regime_id:
                            customer_data['regime_code'] = rec_partner.type_regime_id.id
                        else:
                            raise UserError(_("You must assign the client a type of regimen"))

                        if rec_partner.type_liability_id:
                            customer_data['liability_code'] = rec_partner.type_liability_id.id
                        else:
                            raise UserError(_("You must assign the customer a type of liability"))

                        if rec.partner_id.phone:
                            phone = rec.partner_id.phone
                            if phone[:3] == '+57':
                                temp_phone = ''.join([i for i in phone[3:] if i.isdigit()])
                                phone = temp_phone
                            if phone:
                                customer_data['phone'] = phone

                        if rec.partner_id.street:
                            address = rec.partner_id.street.split(',')[0]
                            if address:
                                customer_data['address'] = address

                        return customer_data
                else:
                    raise UserError(_("The client does not have an identification document number."))
            else:
                raise UserError(_("The client does not have an associated identification document type."))
        return False

    @api.multi
    def get_ei_legal_monetary_totals(self):
        self.ensure_one()
        line_extension_amount = self.amount_untaxed
        tax_exclusive_amount = self.amount_untaxed - self.ei_amount_excluded

        allowance_total_amount = 0.0
        if self.is_universal_discount():
            allowance_total_amount = self.ks_amount_discount

        charge_total_amount = 0.0
        payable_amount = self.ei_amount_total_no_withholding
        tax_inclusive_amount = payable_amount - charge_total_amount + allowance_total_amount

        return {
            "line_extension_value": line_extension_amount,
            "tax_exclusive_value": tax_exclusive_amount,
            "tax_inclusive_value": tax_inclusive_amount,
            "payable_value": payable_amount
        }

    @api.multi
    def get_ei_lines(self):
        lines = []
        for rec in self:
            for invoice_line_id in rec.invoice_line_ids:
                if invoice_line_id.account_id:
                    if not (0 <= invoice_line_id.discount < 100):
                        raise UserError(_("The discount must always be greater than or equal to 0 and less than 100."))

                    price_unit = 100.0 * invoice_line_id.price_subtotal / (invoice_line_id.quantity * (
                            100.0 - invoice_line_id.discount))
                    # The temporary dictionary of elements that belong to the specific line
                    invoice_temps = {}
                    products = {}
                    allowance_charges = {}
                    tax_totals = {'tax_totals': []}
                    products.update({'price_value': price_unit})
                    products.update({'base_quantity': invoice_line_id.quantity})

                    if invoice_line_id.product_id.code:
                        products.update({'product_ref': invoice_line_id.product_id.code})
                    else:
                        raise UserError(_("All products must have an internal reference assigned"))

                    if rec.is_out_country:
                        if invoice_line_id.product_id.brand_name:
                            products.update({'brand_name': invoice_line_id.product_id.brand_name})
                        else:
                            raise UserError(_("Products on export invoices must have a brand name"))

                        if invoice_line_id.product_id.model_name:
                            products.update({'model_name': invoice_line_id.product_id.model_name})
                        else:
                            raise UserError(_("Products on export invoices must have a model name"))

                    products.update({'description': invoice_line_id.name})

                    if invoice_line_id.ei_notes:
                        products.update({'notes': [{'text': invoice_line_id.ei_notes}]})

                    if invoice_line_id.product_id.uom_id.edi_unit_measure_id.id:
                        products.update({'uom_code': invoice_line_id.product_id.uom_id.edi_unit_measure_id.id})
                    elif invoice_line_id.product_id.edi_unit_measure_id.id:
                        # If the setting is used in the Odoo unit of measure, then this field is not required
                        # However, it is left for compatibility with existing fields
                        products.update({'uom_code': invoice_line_id.product_id.edi_unit_measure_id.id})
                    else:
                        raise UserError(_("All products must be assigned a unit of measure (DIAN)"))

                    products.update({'quantity': invoice_line_id.quantity})
                    products.update({'line_extension_value': invoice_line_id.price_subtotal})
                    # [4]: Taxpayer adoption standard ('999')
                    products.update({'item_code': 4})

                    # Discounts
                    if invoice_line_id.discount:
                        discount = True
                        allowance_charges.update({'indicator': False})
                        amount = invoice_line_id.price_subtotal * invoice_line_id.discount / (
                                100.0 - invoice_line_id.discount)
                        base_amount = invoice_line_id.price_subtotal + amount
                        allowance_charge_reason = "Descuento"
                    else:
                        discount = False
                        allowance_charges.update({'indicator': False})
                        amount = 0
                        base_amount = 0
                        allowance_charge_reason = ""

                    products.update({'price_code': 1})  # Commercial value ('01')

                    taxable_amount = invoice_line_id.price_subtotal

                    allowance_charges.update({'base_value': base_amount})
                    allowance_charges.update({'value': amount})
                    allowance_charges.update({'reason': allowance_charge_reason})

                    # Calculate tax totals for invoice line
                    for invoice_line_tax_id in invoice_line_id.invoice_line_tax_ids:
                        tax_total = {}

                        if invoice_line_tax_id.edi_tax_id.id:
                            edi_tax_name = invoice_line_tax_id.edi_tax_id.name
                            tax_name = invoice_line_tax_id.name
                            # The information sent to DIAN should not include the withholdings
                            if edi_tax_name[:4] != 'Rete' and tax_name != 'IVA Excluido':
                                if invoice_line_tax_id.amount_type == 'percent':
                                    tax_total.update({'code': invoice_line_tax_id.edi_tax_id.id})
                                    tax_total.update(
                                        {'tax_value': (taxable_amount * invoice_line_tax_id.amount) / 100.0})
                                    tax_total.update({'taxable_value': taxable_amount})
                                    tax_total.update({'percent': invoice_line_tax_id.amount})
                                    tax_totals['tax_totals'].append(tax_total)
                                elif invoice_line_tax_id.amount_type == 'fixed':
                                    tax_total.update({'code': invoice_line_tax_id.edi_tax_id.id})
                                    tax_total.update(
                                        {'tax_value': invoice_line_id.quantity * invoice_line_tax_id.amount})
                                    tax_total.update({'taxable_value': invoice_line_id.quantity})
                                    # "886","number of international units","NIU"
                                    tax_total.update({'uom_code': 886})
                                    tax_total.update({'unit_value': invoice_line_tax_id.amount})
                                    tax_total.update({'base_uom': "1.000000"})
                                    tax_totals['tax_totals'].append(tax_total)
                                else:
                                    raise UserError(_("Electronic invoicing is not yet compatible with this tax type."))
                        else:
                            raise UserError(_("All taxes must be assigned a tax type (DIAN)."))

                    # UPDATE ALL THE ELEMENTS OF THE PRODUCT
                    invoice_temps.update(products)

                    # UPDATE ALL PRODUCT DISCOUNTS (* ONLY ONE IS SUPPOSED *)
                    if discount:
                        invoice_temps.update({'allowance_charges': [allowance_charges]})

                    # Taxes are attached inside this json
                    if tax_totals['tax_totals']:
                        invoice_temps.update({'tax_totals': tax_totals['tax_totals']})
                    else:
                        invoice_temps.pop("price_code")

                    # Transport compatibility
                    if rec.ei_operation == 'transport':
                        if 'waypoint_id' not in invoice_line_id:
                            raise UserError(_("Transport compatibility is only available with "
                                              "Jorels SAS Freight Transport Module"))

                        if invoice_line_id.waypoint_id:
                            # Check field values
                            if not invoice_line_id.waypoint_id.name_seq:
                                raise UserError(_("A waypoint doesn't have an associated id number"))
                            if not invoice_line_id.waypoint_id.total:
                                raise UserError(_("The waypoint %s doesn't have an total")
                                                % invoice_line_id.waypoint_id.name_seq)
                            if not invoice_line_id.waypoint_id.weight:
                                raise UserError(_("The waypoint %s doesn't have an weight")
                                                % invoice_line_id.waypoint_id.name_seq)

                            # Transport remittance registered in the RNDC
                            invoice_temps.update({'sector_code': 2})

                            # 01. Remittance filing number provided by RNDC
                            # 02. Remittance consecutive number according the internal coding of each transport company.
                            # 03. Freight value to be charged for the transport service.
                            # "767","kilogram","KGM"
                            # "686","gallon","GLL"
                            item_properties = [{
                                'name': '02',
                                'value': invoice_line_id.waypoint_id.name_seq
                            }, {
                                'name': '03',
                                'value': invoice_line_id.waypoint_id.total,
                                'uom_code': '767',
                                'quantity': invoice_line_id.waypoint_id.weight
                            }]

                            if invoice_line_id.waypoint_id.rndc_ingresoid:
                                item_properties.append({
                                    'name': '01',
                                    'value': invoice_line_id.waypoint_id.rndc_ingresoid
                                })

                            invoice_temps.update({'item_properties': item_properties})

                        else:
                            # Additional service
                            invoice_temps.update({'sector_code': 1})

                    # Mandates compatibility
                    if rec.ei_operation == 'mandates':
                        raise UserError(_("Electronic invoicing does not yet support mandates"))

                    lines.append(invoice_temps)

        return lines

    # Calculation of withholdings, excluded, etc.
    @api.one
    def _compute_amount(self):
        res = super(AccountInvoice, self)._compute_amount()

        amount_tax_withholding = 0
        amount_tax_no_withholding = 0
        amount_excluded = 0
        for tax_line_id in self.tax_line_ids:
            if tax_line_id.tax_id.edi_tax_id:
                edi_tax_name = tax_line_id.tax_id.edi_tax_id.name
                tax_name = tax_line_id.tax_id.name
                if tax_name == 'IVA Excluido':
                    amount_excluded = amount_excluded + tax_line_id.base
                elif edi_tax_name[:4] == 'Rete':
                    amount_tax_withholding = amount_tax_withholding + tax_line_id.amount_total
                else:
                    amount_tax_no_withholding = amount_tax_no_withholding + tax_line_id.amount_total
            else:
                tax_name = tax_line_id.tax_id.name
                if tax_name == 'IVA Excluido':
                    amount_excluded = amount_excluded + tax_line_id.base
                elif tax_name[:3] == 'Rte':
                    amount_tax_withholding = amount_tax_withholding + tax_line_id.amount_total
                else:
                    amount_tax_no_withholding = amount_tax_no_withholding + tax_line_id.amount_total

        self.ei_amount_tax_withholding = amount_tax_withholding
        self.ei_amount_tax_no_withholding = amount_tax_no_withholding
        self.ei_amount_total_no_withholding = self.amount_untaxed + self.ei_amount_tax_no_withholding
        self.ei_amount_excluded = amount_excluded

        if self.is_universal_discount():
            if not ('ks_global_tax_rate' in self):
                self.ks_calculate_discount()
            sign = self.type in ['in_refund', 'out_refund'] and -1 or 1
            self.amount_total_company_signed = self.amount_total * sign
            self.amount_total_signed = self.amount_total * sign

            self.ei_amount_total_no_withholding = self.amount_untaxed + \
                                                  self.ei_amount_tax_no_withholding - \
                                                  self.ks_amount_discount

        # Value in letters
        decimal_part, integer_part = math.modf(self.amount_total)
        if decimal_part:
            decimal_part = round(decimal_part * math.pow(10, self.currency_id.decimal_places))
        if integer_part:
            lang = self.partner_id.lang if self.partner_id.lang else 'es_CO'

            self.value_letters = num2words(integer_part, lang=lang).upper() + ' ' + \
                                 self.currency_id.currency_unit_label.upper()
            if decimal_part:
                self.value_letters = self.value_letters + ', ' + \
                                     num2words(decimal_part, lang=lang).upper() + ' ' + \
                                     self.currency_id.currency_subunit_label.upper() + '.'

        return res

    @api.multi
    def get_ei_payment_form(self):
        for rec in self:
            if rec.date_invoice and rec.date_due:
                if rec.date_invoice >= rec.date_due:
                    # Cash
                    duration_measure = 0
                else:
                    # Credit
                    duration_measure = (rec.date_due - rec.date_invoice).days
                payment_due_date = fields.Date.to_string(rec.date_due)
            else:
                _logger.debug("The invoice or payment date is not valid")
                # Cash
                duration_measure = 0
                payment_due_date = fields.Date.to_string(rec.date_invoice)

            return {
                'code': rec.payment_form_id.id,
                'method_code': rec.payment_method_id.id,
                'due_date': payment_due_date,
                'duration_days': duration_measure
            }

    @api.multi
    def get_ei_type_document_id(self):
        self.ensure_one()
        type_documents_env = self.env['l10n_co_edi_jorels.type_documents']
        # For now the document type is always
        # Electronic invoicing (Code '01')
        # Export electronic invoicing (Code '02')
        # Credit note (Code '91')
        # Or Debit note (Code '92')
        # The contingency and others are pending review
        type_edi_document = self.get_type_edi_document()
        if type_edi_document != 'none':
            if type_edi_document == 'invoice':
                # Sales invoice
                if not self.journal_id.is_out_country:
                    type_documents_rec = type_documents_env.search([('code', '=', '01')])
                else:
                    type_documents_rec = type_documents_env.search([('code', '=', '02')])
            elif type_edi_document == 'credit_note':
                # Credit note
                type_documents_rec = type_documents_env.search([('code', '=', '91')])
            elif type_edi_document == 'debit_note':
                # Debit note
                type_documents_rec = type_documents_env.search([('code', '=', '92')])
            else:
                raise UserError(_("This type of document does not need to be sent to DIAN"))
        else:
            raise UserError(_("This type of document does not need to be sent to DIAN"))

        self.ei_type_document_id = type_documents_rec.id

        return self.ei_type_document_id.id

    @api.multi
    def get_ei_sync(self):
        self.ensure_one()
        self.ei_sync = self.ei_is_not_test
        return self.ei_sync

    @api.multi
    def get_ei_is_not_test(self):
        self.ensure_one()
        return self.ei_is_not_test

    @api.depends('journal_id')
    def _compute_resolution(self):
        for rec in self:
            type_edi_document = rec.get_type_edi_document()
            if type_edi_document != 'none':
                if type_edi_document == 'invoice' and rec.journal_id.sequence_id.resolution_id:
                    # Sales invoice
                    rec.resolution_id = rec.journal_id.sequence_id.resolution_id.id
                elif type_edi_document == 'credit_note' and rec.journal_id.refund_sequence_id.resolution_id:
                    # Credit note
                    rec.resolution_id = rec.journal_id.refund_sequence_id.resolution_id.id
                elif type_edi_document == 'debit_note' and rec.journal_id.debitnote_sequence_id.resolution_id:
                    # Debit note
                    rec.resolution_id = rec.journal_id.debitnote_sequence_id.resolution_id.id
                else:
                    rec.resolution_id = None
                    _logger.debug("This type of document does not have a DIAN resolution assigned")
            else:
                rec.resolution_id = None

    @api.depends('number', 'reference')
    def compute_number_formatted(self):
        for rec in self:
            if rec.type in ['out_invoice', 'out_refund']:
                prefix = rec.resolution_id.resolution_prefix
                ei_number = ''
                number_formatted = ''

                if rec.number and prefix:
                    # Remove non-alphanumeric characters
                    number = re.sub(r'\W+', '', rec.number)
                    len_prefix = len(prefix)
                    len_number = len(number)
                    if 0 < len_prefix < len_number and number[0:len_prefix] == prefix:
                        number_unformatted = ''.join([i for i in number[len_prefix:] if i.isdigit()])
                        if number_unformatted:
                            ei_number = str(int(number_unformatted))
                            number_formatted = prefix + ei_number

                if ei_number and number_formatted:
                    rec.ei_number = ei_number
                    rec.number_formatted = number_formatted
                else:
                    rec.ei_number = ''
                    rec.number_formatted = ''
                    _logger.debug('Compute number format: Error.')
            elif rec.type in ['in_invoice', 'in_refund']:
                rec.ei_number = rec.reference
                rec.number_formatted = rec.reference
            else:
                rec.ei_number = ''
                rec.number_formatted = ''

    @api.model
    def _prepare_refund(self, invoice, date_invoice=None, date=None, description=None, journal_id=None):
        values = super(AccountInvoice, self)._prepare_refund(invoice, date_invoice, date, description, journal_id)

        if description:
            ei_correction_concept_search = self.env['l10n_co_edi_jorels.correction_concepts'].search([
                ('name', '=', description),
                ('type_document_id', '=', 5)
            ])
            if ei_correction_concept_search:
                values['ei_correction_concept_credit_id'] = ei_correction_concept_search[0].id

        values['is_out_country'] = invoice.is_out_country

        return values

    @api.model
    def _prepare_debitnote(self, invoice, date_invoice=None, date=None, description=None, journal_id=None):
        values = super(AccountInvoice, self)._prepare_debitnote(invoice, date_invoice, date, description, journal_id)

        values['is_out_country'] = invoice.is_out_country

        return values

    @api.depends('ei_type_document_id', 'ei_correction_concept_credit_id', 'ei_correction_concept_debit_id')
    def compute_ei_correction_concept_id(self):
        for rec in self:
            if rec.ei_type_document_id.id == 5:
                rec.ei_correction_concept_id = rec.ei_correction_concept_credit_id.id
            elif rec.ei_type_document_id.id == 6:
                rec.ei_correction_concept_id = rec.ei_correction_concept_debit_id.id
            else:
                rec.ei_correction_concept_id = None

    @api.multi
    def get_operation_code(self):
        self.ensure_one()
        operation = {
            'aiu': 9,
            'standard': 10,
            'mandates': 11,
            'transport': 12,
            'exchange': 25
        }

        type_edi_document = self.get_type_edi_document()
        if type_edi_document == 'credit_note':
            return 14 if self.ei_is_correction_without_reference else 13
        elif type_edi_document == 'debit_note':
            return 17 if self.ei_is_correction_without_reference else 16
        else:
            return operation[self.ei_operation]

    @api.multi
    def get_json_request(self):
        for rec in self:
            # If it is a sales invoice or credit note or debit note.
            if rec.type == 'out_invoice' or rec.type == 'out_refund':
                # Important for compatibility with old fields,
                # third-party modules or manual changes to the database
                if not rec.ei_number or not rec.number_formatted:
                    rec.compute_number_formatted()

                # Check resolution
                if not rec.resolution_id:
                    raise UserError(_("This type of document does not have a DIAN resolution assigned"))

                json_request = {
                    'number': rec.ei_number,
                    'type_document_code': rec.get_ei_type_document_id(),
                    'resolution_code': rec.resolution_id.resolution_id,
                    'sync': rec.get_ei_sync(),
                    'customer': rec.get_ei_customer(),
                    'operation_code': rec.get_operation_code()
                }

                # Due date
                if rec.date_due:
                    json_request['due_date'] = fields.Date.to_string(rec.date_due)

                # Period
                if rec.date_start and rec.date_end:
                    json_request['period'] = {
                        'date_start': fields.Date.to_string(rec.date_start),
                        'date_end': fields.Date.to_string(rec.date_end)
                    }

                # Order reference
                if rec.order_ref_number:
                    json_request['order_reference'] = {
                        'number': rec.order_ref_number
                    }
                    if rec.order_ref_date:
                        json_request['order_reference']['issue_date'] = fields.Date.to_string(rec.order_ref_date)

                # Multi-currency compatibility
                if rec.currency_id and rec.company_id and rec.currency_id != rec.company_id.currency_id:
                    company_currency_code = rec.company_id.currency_id.name
                    invoice_currency_code = rec.currency_id.name

                    type_currencies_env = self.env['l10n_co_edi_jorels.type_currencies']
                    company_currency_search = type_currencies_env.search([('code', '=', company_currency_code)])
                    invoice_currency_search = type_currencies_env.search([('code', '=', invoice_currency_code)])

                    # The if is to make sure the name in currency_id,
                    # have a match in the code in type_currencies of the DIAN
                    if company_currency_search and invoice_currency_search:

                        # The inverse of Odoo,
                        # for example for company = COP and invoice = USD,
                        # the cup must be USD-> COP, not COP-> USD as it comes by default
                        # This can lead to rounding errors that need to be reviewed in more detail.
                        #
                        # There is an OCA module that allows you to use reverse cups and avoid these problems,
                        # but it was found that it could cause conflicts in the automatic calculation of prices.
                        #
                        # For now it is checked if there is a hypothetical Boolean field rate_inverted, as it would be
                        # the case of the OCA module; although it is not considered a true solution to the problem.
                        # The best would be 'maybe' to raise the precision of the 'rate' field so that even at a
                        # investment the value remains within the expected margin.
                        if hasattr(rec.currency_id, 'rate_inverted') and rec.currency_id.rate_inverted:
                            calculation_rate = rec.currency_id.rate
                        else:
                            calculation_rate = 1.0 / rec.currency_id.rate

                        rate_date = rec._get_currency_rate_date() or fields.Date.context_today(self)

                        json_request['currency_code'] = invoice_currency_search.id
                        json_request['exchange_rate'] = {
                            'code': company_currency_search.id,
                            'rate': calculation_rate,
                            'date': str(rate_date)
                        }
                    else:
                        raise UserError(_("A currency type in Odoo does not correspond to any DIAN currency type"))

                if self.is_universal_discount():
                    if rec.ks_amount_discount:
                        allowance_charges = []
                        allowance_charge = {
                            'indicator': False,
                            'discount_code': 2,
                            'base_value': rec.amount_untaxed,
                            'value': rec.ks_amount_discount,
                            'reason': 'Descuento general'
                        }
                        allowance_charges.append(allowance_charge)
                        json_request['allowance_charges'] = allowance_charges

                # json_request y billing_reference
                billing_reference = False
                type_edi_document = rec.get_type_edi_document()
                if type_edi_document != 'none':
                    json_request['legal_monetary_totals'] = rec.get_ei_legal_monetary_totals()
                    json_request['lines'] = rec.get_ei_lines()
                    json_request['payment_forms'] = [rec.get_ei_payment_form()]
                    if type_edi_document == 'invoice':
                        # Sales invoice
                        billing_reference = False
                    elif type_edi_document == 'credit_note':
                        # Credit note
                        billing_reference = True
                    elif type_edi_document == 'debit_note':
                        # Debit note
                        billing_reference = True
                else:
                    raise UserError(_("This type of document does not need to be sent to DIAN"))

                # Billing reference
                if billing_reference:
                    rec.compute_ei_correction_concept_id()
                    if rec.ei_correction_concept_id:
                        json_request["discrepancy"] = {
                            # "reference": None,
                            "correction_code": rec.ei_correction_concept_id.id,
                            "description": rec.name if rec.name else ''
                        }
                    else:
                        raise UserError(_("You need to select a correction code first"))

                    if not rec.ei_is_correction_without_reference:
                        invoice_rec = self.env['account.invoice'].search([('number', '=', rec.origin)])
                        if invoice_rec and invoice_rec.ei_uuid:
                            json_request["reference"] = {
                                "number": invoice_rec.number_formatted,
                                "uuid": invoice_rec.ei_uuid,
                                "issue_date": fields.Date.to_string(invoice_rec.ei_issue_date)
                            }
                        else:
                            raise UserError(_("The reference invoice has not yet been validated before the DIAN"))

                if rec.name or rec.comment:
                    notes = []
                    if rec.name:
                        notes.append({'text': rec.name})
                    if rec.comment:
                        comment = re.sub(r'<.*?>', '', rec.comment)
                        if comment:
                            notes.append({'text': comment})
                    json_request['notes'] = notes
            else:
                raise UserError(_("This type of document does not need to be sent to DIAN"))

            return json_request

    # TO DO: It can be made more efficient by calling this function fewer times,
    # when the electronic invoice is processed. Ideally, it should be just one time
    @api.multi
    def get_type_edi_document(self):
        type_edi_document = 'none'
        for rec in self:
            if rec.type == 'out_invoice':
                if rec.origin:
                    if rec.debit_invoice_id:
                        # Debit note
                        type_edi_document = 'debit_note'
                    else:
                        # Sales invoice
                        type_edi_document = 'invoice'
                # Sales invoice
                else:
                    type_edi_document = 'invoice'
            elif rec.type == 'out_refund':
                # Credit note
                type_edi_document = 'credit_note'
        return type_edi_document

    @api.multi
    def validate_dian_generic(self, is_test):
        for rec in self:
            if not rec.company_id.ei_enable:
                continue

            # raise Warning(json.dumps(rec.get_json_request(), indent=2, sort_keys=False))
            _logger.debug("DIAN Validation Request: %s", json.dumps(rec.get_json_request(), indent=2, sort_keys=False))

            try:
                type_edi_document = rec.get_type_edi_document()
                if type_edi_document != 'none':
                    requests_data = rec.get_json_request()

                    if rec.company_id.api_key:
                        token = rec.company_id.api_key
                    else:
                        raise UserError(_("You must configure a token"))

                    api_url = self.env['ir.config_parameter'].sudo().get_param('jorels.edipo.api_url',
                                                                               'https://edipo.jorels.com')
                    params = {'token': token}
                    header = {
                        "accept": "application/json",
                        "Content-Type": "application/json"
                    }

                    if rec.is_out_country:
                        params['export'] = True

                    api_url = api_url + "/" + type_edi_document

                    if is_test or not rec.ei_is_not_test:
                        if rec.company_id.test_set_id:
                            test_set_id = rec.company_id.test_set_id
                            params['test_set_id'] = test_set_id
                        else:
                            raise UserError(_("You have not configured a 'TestSetId'."))

                    _logger.debug('API URL: %s', api_url)

                    response = requests.post(api_url,
                                             json.dumps(requests_data),
                                             headers=header,
                                             params=params).json()
                    _logger.debug('API Response: %s', response)

                    if 'detail' in response:
                        raise UserError(response['detail'])
                    if 'message' in response:
                        if response['message'] == 'Unauthenticated.' or response['message'] == '':
                            raise UserError(_("Authentication error with the API"))
                        else:
                            if 'errors' in response:
                                raise UserError(response['message'] + '/ errors: ' + str(response['errors']))
                            else:
                                raise UserError(response['message'])
                    elif 'is_valid' in response:
                        rec.write_response(response, json.dumps(requests_data, indent=2, sort_keys=False))
                        if response['is_valid']:
                            self.env.user.notify_success(message=_("The validation at DIAN has been successful."))
                        elif 'uuid' in response:
                            if response['uuid'] != "":
                                if not rec.ei_is_not_test:
                                    self.env.user.notify_success(message=_("Document sent to DIAN in habilitation."))
                                else:
                                    temp_message = {rec.ei_status_message, rec.ei_errors_messages,
                                                    rec.ei_status_description, rec.ei_status_code}
                                    raise UserError(str(temp_message))
                            else:
                                raise UserError(_('A valid UUID was not obtained. Try again.'))
                        else:
                            raise UserError(_('The document could not be validated in DIAN.'))
                    else:
                        raise UserError(_("No logical response was obtained from the API."))
                else:
                    raise UserError(_("This type of document does not need to be sent to the DIAN"))
            except Exception as e:
                _logger.debug("Failed to process the request: %s", e)
                raise UserError(_("Failed to process the request: %s") % e)

            if not is_test and not rec.ei_attached_document_base64_bytes:
                rec.status_document_log()
                if not rec.ei_attached_document_base64_bytes:
                    _logger.error('Unable to obtain an attached document.')

    @api.multi
    def validate_dian(self):
        for rec in self:
            rec.validate_dian_generic(False)
            rec.write({'state': 'open'})

    @api.multi
    def validate_dian_test(self):
        for rec in self:
            rec.validate_dian_generic(True)
            rec.write({'state': 'open'})

    @api.multi
    def skip_validate_dian(self):
        for rec in self:
            rec.write({'state': 'open'})
            self.env.user.notify_warning(message=_("The validation process has been skipped."))

    @api.multi
    def skip_validate_dian_production(self):
        for rec in self:
            rec.skip_validate_dian()

    @api.multi
    def action_invoice_open(self):
        res = super(AccountInvoice, self).action_invoice_open()

        to_edi = self.filtered(lambda inv: inv.company_id.ei_enable
                                           and inv.type in ('out_invoice', 'out_refund')
                                           and not inv.ei_is_valid
                                           and not inv.is_journal_pos())
        if to_edi:
            # Invoices in DIAN cannot be validated with zero total
            to_paid_invoices = to_edi.filtered(lambda inv: inv.state == 'paid')
            if to_paid_invoices:
                raise UserError(_('Please check your invoice again. Are you really billing something?'))

            # Validate invoices
            to_electronic_invoices = to_edi.filtered(lambda inv: inv.state == 'open'
                                                                 and not inv.company_id.enable_validate_state)
            if to_electronic_invoices:
                to_electronic_invoices.filtered(lambda inv: inv.write({'ei_is_not_test': inv.company_id.is_not_test}))

                # Production invoices
                to_production_invoices = to_electronic_invoices.filtered(lambda inv: inv.ei_is_not_test)
                if to_production_invoices:
                    to_production_invoices.validate_dian_generic(False)
                    to_mass_send = to_production_invoices.filtered(lambda inv: inv.company_id.enable_mass_send_print)
                    if to_mass_send:
                        try:
                            to_mass_send.mass_send_print()
                        except Exception as e:
                            self.env.user.notify_danger(message=_('The invoice email could not be sent'))
                            _logger.debug("The invoice email could not be sent")
                            _logger.error('mass_send_print error: %s' % e)

                # Test invoices
                to_test_invoices = to_electronic_invoices.filtered(lambda inv: not inv.ei_is_not_test)
                if to_test_invoices:
                    to_test_invoices.validate_dian_generic(True)

        return res

    @api.multi
    def status_document(self):
        for rec in self:
            if not rec.company_id.ei_enable:
                continue

            try:
                # This line ensures that the electronic fields of the invoice are updated in Odoo, before the request
                requests_data = rec.get_json_request()
                _logger.debug('Customer data: %s', requests_data)

                type_edi_document = rec.get_type_edi_document()
                if type_edi_document != 'none':
                    if rec.ei_zip_key or rec.ei_uuid:
                        requests_data = {}
                        _logger.debug('API Requests: %s', requests_data)

                        if rec.company_id.api_key:
                            token = rec.company_id.api_key
                        else:
                            raise UserError(_("You must configure a token"))

                        api_url = self.env['ir.config_parameter'].sudo().get_param('jorels.edipo.api_url',
                                                                                   'https://edipo.jorels.com')
                        params = {
                            'token': token,
                            'environment': 1 if rec.ei_is_not_test else 2
                        }
                        header = {"accept": "application/json", "Content-Type": "application/json"}

                        if rec.ei_zip_key:
                            api_url = api_url + "/zip/" + rec.ei_zip_key
                        else:
                            api_url = api_url + "/document/" + rec.ei_uuid

                        _logger.debug('API URL: %s', api_url)

                        response = requests.post(api_url,
                                                 json.dumps(requests_data),
                                                 headers=header,
                                                 params=params).json()
                        _logger.debug('API Response: %s', response)

                        if 'detail' in response:
                            raise UserError(response['detail'])
                        if 'message' in response:
                            if response['message'] == 'Unauthenticated.' or response['message'] == '':
                                raise UserError(_("Authentication error with the API"))
                            else:
                                if 'errors' in response:
                                    raise UserError(response['message'] + '/ errors: ' + str(response['errors']))
                                else:
                                    raise UserError(response['message'])
                        elif 'is_valid' in response:
                            rec.write_response(response, json.dumps(requests_data, indent=2, sort_keys=False))
                            if response['is_valid']:
                                self.env.user.notify_info(message=_("Validation in DIAN has been successful."))
                            elif 'zip_key' in response or 'uuid' in response:
                                if response['zip_key'] is not None or response['uuid'] is not None:
                                    if not rec.ei_is_not_test:
                                        self.env.user.notify_info(message=_("Document sent to DIAN in testing."))
                                    else:
                                        temp_message = {rec.ei_status_message, rec.ei_errors_messages,
                                                        rec.ei_status_description, rec.ei_status_code}
                                        raise UserError(str(temp_message))
                                else:
                                    raise UserError(_('A valid Zip key or UUID was not obtained. Try again.'))
                            else:
                                raise UserError(_('The document could not be validated in DIAN.'))
                        else:
                            raise UserError(_("No logical response was obtained from the API"))
                    else:
                        raise UserError(_("A Zip key or UUID is required to check the status of the document."))
                else:
                    raise UserError(_("This type of document does not need to be sent to the DIAN"))
            except Exception as e:
                _logger.debug("Failed to process the request: %s", e)
                raise UserError(_("Failed to process the request: %s") % e)

    @api.multi
    def status_document_log(self):
        for rec in self:
            if not rec.company_id.ei_enable:
                continue

            try:
                # This line ensures that the electronic fields of the invoice are updated in Odoo,
                # before request
                requests_data = rec.get_json_request()
                _logger.debug('Customer data: %s', requests_data)

                type_edi_document = rec.get_type_edi_document()
                if type_edi_document != 'none':
                    if rec.number_formatted:
                        requests_data = {}
                        _logger.debug('API Requests: %s', requests_data)

                        if rec.company_id.api_key:
                            token = rec.company_id.api_key
                        else:
                            raise UserError(_("You must configure a token"))

                        api_url = self.env['ir.config_parameter'].sudo().get_param('jorels.edipo.api_url',
                                                                                   'https://edipo.jorels.com')
                        params = {'token': token}
                        header = {"accept": "application/json", "Content-Type": "application/json"}

                        api_url = api_url + "/logs/" + rec.number_formatted

                        _logger.debug('API URL: %s', api_url)

                        response = requests.post(api_url,
                                                 json.dumps(requests_data),
                                                 headers=header,
                                                 params=params).json()
                        _logger.debug('API Response: %s', response)

                        if 'detail' in response:
                            raise UserError(response['detail'])
                        if 'message' in response:
                            if response['message'] == 'Unauthenticated.' or response['message'] == '':
                                self.env.user.notify_warning(message=_("Authentication error with the API"))
                                _logger.debug("Authentication error with the API")
                            else:
                                if 'errors' in response:
                                    self.env.user.notify_warning(
                                        message=response['message'] + '/ errors: ' + str(response['errors']))
                                    _logger.debug(response['message'] + '/ errors: ' + str(response['errors']))
                                else:
                                    self.env.user.notify_warning(message=response['message'])
                                    _logger.debug(response['message'])
                        elif response and ('is_valid' in response[0]):
                            success = False
                            for log in response:
                                if log['is_valid']:
                                    json_request = json.loads(json.dumps(log))
                                    rec.ei_is_valid = json_request['is_valid']
                                    if json_request['algorithm']:
                                        rec.ei_algorithm = json_request['algorithm']
                                    if json_request['uuid']:
                                        rec.ei_uuid = json_request['uuid']
                                    if json_request['issue_date']:
                                        rec.ei_issue_date = json_request['issue_date']
                                    if json_request['zip_key']:
                                        rec.ei_zip_key = json_request['zip_key']
                                    if json_request['xml_name']:
                                        rec.ei_xml_name = json_request['xml_name']
                                    if json_request['zip_name']:
                                        rec.ei_zip_name = json_request['zip_name']
                                    if json_request['xml_base64_bytes']:
                                        rec.ei_xml_base64_bytes = json_request['xml_base64_bytes']
                                    if json_request['qr_data']:
                                        rec.ei_qr_data = json_request['qr_data']
                                    if json_request['application_response_base64_bytes']:
                                        rec.ei_application_response_base64_bytes = json_request[
                                            'application_response_base64_bytes']
                                    if json_request['attached_document_base64_bytes']:
                                        rec.ei_attached_document_base64_bytes = json_request[
                                            'attached_document_base64_bytes']
                                    if json_request['pdf_base64_bytes']:
                                        rec.ei_pdf_base64_bytes = json_request['pdf_base64_bytes']
                                    if json_request['zip_base64_bytes']:
                                        rec.ei_zip_base64_bytes = json_request['zip_base64_bytes']
                                    if json_request['signature']:
                                        rec.ei_signature = json_request['signature']

                                        # QR code
                                        qr = qrcode.QRCode(
                                            version=1,
                                            error_correction=qrcode.constants.ERROR_CORRECT_M,
                                            box_size=2,
                                            border=2,
                                        )
                                        qr.add_data(rec.ei_qr_data)
                                        qr.make(fit=True)
                                        img = qr.make_image()
                                        temp = BytesIO()
                                        img.save(temp, format="PNG")
                                        qr_image = base64.b64encode(temp.getvalue())
                                        rec.ei_qr_image = qr_image

                                    success = True
                                    break
                            if success:
                                self.env.user.notify_info(message=_("Validation in DIAN has been successful."))
                                _logger.debug("Validation in DIAN has been successful.")
                            else:
                                self.env.user.notify_warning(message=_("The document has not been validated."))
                                _logger.debug("The document has not been validated.")
                        else:
                            self.env.user.notify_warning(message=_("The document could not be consulted."))
                            _logger.debug("The document could not be consulted.")
                    else:
                        self.env.user.notify_warning(
                            message=_("A number is required to verify the status of the document."))
                        _logger.debug("A number is required to verify the status of the document.")
                else:
                    self.env.user.notify_warning(message=_("This type of document does not need to be sent to DIAN."))
                    _logger.debug("This type of document does not need to be sent to DIAN.")
            except Exception as e:
                self.env.user.notify_warning(message=_("Failed to process the request"))
                _logger.debug("Failed to process the request: %s", e)

    @api.depends('ei_attached_document_base64_bytes')
    def _is_attached_document_matched(self):
        for rec in self:
            if not rec.company_id.ei_enable:
                continue

            if rec.ei_attached_document_base64_bytes:
                with BytesIO(base64.b64decode(rec.ei_attached_document_base64_bytes)) as file:
                    search_ok = False
                    for line in file:
                        search_string = '<cbc:ParentDocumentID>' + rec.number_formatted + '</cbc:ParentDocumentID>'
                        if search_string in str(line):
                            search_ok = True
                            break
                    rec.is_attached_document_matched = search_ok
            else:
                rec.is_attached_document_matched = False

    @api.multi
    def message_update(self, msg_dict, update_vals=None):
        """Check DIAN events from email content"""
        res = super(AccountInvoice, self).message_update(msg_dict, update_vals)

        for rec in self:
            if not rec.company_id.ei_enable:
                continue

            csi = rec.partner_id.customer_software_id
            rec.event = csi.get_event(msg_dict)

            # TO DO:
            # example: msg_dict['date'] = '2021-07-20 01:15:20'
            # rec.event_date = msg_dict['date']

            _logger.debug("Mail event. Invoice: %s, Event: %s" % (rec.number_formatted, rec.event))

        return res
