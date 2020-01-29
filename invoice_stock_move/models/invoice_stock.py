# -*- coding: utf-8 -*-
#############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2019-TODAY Cybrosys Technologies(<https://www.cybrosys.com>).
#    Author: Saritha Sahadevan @cybrosys(odoo@cybrosys.com)
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################
from odoo.exceptions import UserError
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)
 


class InvoiceStockMove(models.Model):
    _inherit = 'account.move'

    @api.onchange('type','invoice_line_ids','id','state')
    def onchange_invoice_type(self):
 
        if self.env.context.get('default_team_id',False) or (self._fields.get('team_id',False) and self.invoice_origin): # is a invoice from sale order 
            transfer_state = 'make_the_transfer_from_sale'
            self.picking_type_id = False
            self.transfer_state = transfer_state
            return
        if self.env.context.get('default_purchase_id',False) or (self._fields.get('purchase_id',False) and self.purchase_id): # is a invoice from purchase order
            transfer_state = 'make_the_transfer_from_purchase'
            self.picking_type_id = False
            self.transfer_state = transfer_state
            return

        transfer_state = 'nothing_to_transfer'
        for line in self.invoice_line_ids:
            if line.product_id:
                if line.product_id.type == 'product':
                    transfer_state = 'not_initiated'
                    break
        self.transfer_state = transfer_state
        domain=[('id','=',0)]
        if transfer_state == 'not_initiated':
            if self.type in ['out_invoice','out_receipt']:
                domain=[('code','=','outgoing')]
            
            elif self.type in ['in_invoice','in_receipt']:
                domain=[('code','=','incoming')]
            if not self.picking_type_id:
                type_obj = self.env['stock.picking.type']
                company_id = self.env.context.get('company_id') or self.env.user.company_id.id
                full_domain = domain+ [('warehouse_id.company_id', '=', company_id)]
                self.picking_type_id = type_obj.search(full_domain, limit=1)
    #            types = type_obj.search([('code', '=', 'incoming'), ('warehouse_id', '=', False)])
        return {'domain': {'picking_type_id': domain}}

    @api.model
    def _get_default_statep(self):
        if self._context.get('active_model') == 'sale.order':
            return 'make_the_transfer_from_sale'
        if self._context.get('active_model') == 'purchase.order':
            return 'make_the_transfer_from_purchase'
        
        return 'created_from_another_object_edit_lines_to_make_tranfer'



    transfer_state = fields.Selection([('not_initiated','not_initiated'),
                                       ('nothing_to_transfer','nothing_to_transfer'),
                                       ('transfered','transfered'),
                                       ('waiting_transfer','waiting_transfer'),
                                       ('make_the_transfer_from_purchase','make_the_transfer_from_purchase'),
                                       ('make_the_transfer_from_sale','make_the_transfer_from_sale'),
                                       ('created_from_another_object_edit_lines_to_make_tranfer','created_from_another_object_edit_lines_to_make_tranfer'),
                                       ],string='Stock Transfer State', help='If the transfer form invoice was done or not',
                                       default=_get_default_statep, copy=False)
    picking_type_id = fields.Many2one('stock.picking.type', 'Picking Type', required=False,
                                      help="This will determine picking type of incoming shipment",
                                      copy=False, )

    invoice_picking_id = fields.Many2one('stock.picking', string="Picking Id",copy=False) 
    picking_status = fields.Selection(related='invoice_picking_id.state')


    def action_stock_move(self):
        "will be executed at the pressing of transfer button"
        for order in self:  # order is account.move meaning also invoice
            if not self.invoice_picking_id:
                if self.type in ['out_invoice','out_receipt']:
                    location_dest_id = self.partner_id.property_stock_customer.id 
                    location_id = self.picking_type_id.default_location_src_id.id
                elif self.type in ['in_invoice','in_receipt']:
                    location_dest_id = self.picking_type_id.default_location_dest_id.id
                    location_id = self.partner_id.property_stock_supplier.id
                else:
                    _logger.error(f'is something wrong, transfer should exist only for invoices self={self}') 
                    self.transfer_state = 'nothing_to_transfer'
                    return

                pick = {
                    'picking_type_id': self.picking_type_id.id,
                    'partner_id': self.partner_id.id,
                    'origin': self.type+self.name,
                    'location_dest_id': location_dest_id,
                    'location_id': location_id
                }
                picking = self.env['stock.picking'].create(pick)
                self.invoice_picking_id = picking.id
                self.transfer_state = 'transfered'
                moves = order.invoice_line_ids.filtered(lambda r: r.product_id.type in ['product', 'consu'])._create_stock_moves(picking)

                move_ids = moves._action_confirm()
                move_ids._action_assign()

    def action_view_picking(self):
        action = self.env.ref('stock.action_picking_tree_ready')
        result = action.read()[0]
        result.pop('id', None)
        result['context'] = {}
        result['domain'] = [('id', '=', self.invoice_picking_id.id)]
        pick_ids = sum([self.invoice_picking_id.id])
        if pick_ids:
            res = self.env.ref('stock.view_picking_form', False)
            result['views'] = [(res and res.id or False, 'form')]
            result['res_id'] = pick_ids or False
        return result


class SupplierInvoiceLine(models.Model):
    _inherit = 'account.move.line'

    def _create_stock_moves(self, picking):
        moves = self.env['stock.move']
        done = self.env['stock.move'].browse()
        for line in self:
            price_unit = line.price_unit
            template = {
                'name': line.name or '',
                'product_id': line.product_id.id,
                'product_uom': line.product_uom_id.id,
#                 'location_id': line.move_id.partner_id.property_stock_supplier.id,
#                 'location_dest_id': picking.picking_type_id.default_location_dest_id.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'picking_id': picking.id,
                # 'move_dest_id': False,
                'state': 'draft',
                'company_id': line.move_id.company_id.id,
                'price_unit': price_unit,
                'picking_type_id': picking.picking_type_id.id,
                # 'procurement_id': False,
                'route_ids': 1 and [
                    (6, 0, [x.id for x in self.env['stock.location.route'].search([('id', 'in', (2, 3))])])] or [],
                'warehouse_id': picking.picking_type_id.warehouse_id.id,
            }
            print(template['route_ids'], "two")
            diff_quantity = line.quantity
            tmp = template.copy()
            tmp.update({
                'product_uom_qty': diff_quantity,
            })
            template['product_uom_qty'] = diff_quantity
            done += moves.create(template)
        return done
