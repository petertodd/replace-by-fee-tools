#!/usr/bin/python3
# Copyright (C) 2015 Peter Todd <pete@petertodd.org>
#
# This file is subject to the license terms in the LICENSE file found in the
# top-level directory of this distribution.

import argparse
import binascii
import bitcoin
import bitcoin.rpc
import logging
import math

from bitcoin.core import b2x, b2lx, lx, str_money_value, COIN, CMutableTransaction, CMutableTxIn, CMutableTxOut
from bitcoin.wallet import CBitcoinAddress

DUST = int(0.0001 * COIN)

parser = argparse.ArgumentParser(description="Pay multiple recipients, taking advantage of full-RBF tx combining")
parser.add_argument('-v', action='store_true',
                    dest='verbose',
                    help='Verbose')
parser.add_argument('-t', action='store_true',
                    dest='testnet',
                    help='Enable testnet')
parser.add_argument('-n', action='store_true',
                    dest='dryrun',
                    help="Dry-run; don't actually send the transaction")
parser.add_argument('-s', action='store_true',
                    dest='first_seen_safe',
                    help="First-seen-safe rules; do not decrease the value of any txouts")
parser.add_argument('-p', action='store', type=str,
                    default=None,
                    dest='prev_txid',
                    help='Previous txid to add new output to; creates new tx if not set')
parser.add_argument('--relay-bw-feerate', action='store', type=float,
                    default=0.000011,
                    help='Relay bandwidth fee per KB')
parser.add_argument('address', action='store', type=str,
                    help='Destination address')
parser.add_argument('amount', action='store', type=float,
                    help='Amount to send')
args = parser.parse_args()

if args.verbose:
    logging.root.setLevel('DEBUG')

if args.testnet:
    bitcoin.SelectParams('testnet')

rpc = bitcoin.rpc.Proxy()

tx1 = None
if args.prev_txid is not None:
    try:
        args.prev_txid = lx(args.prev_txid)
    except ValueError as err:
        parser.error('Invalid txid: %s' % str(err))

    if len(args.prev_txid) != 32:
        parser.error('Invalid txid: Wrong length.')

    tx1 = rpc.getrawtransaction(args.prev_txid)

tx2 = CMutableTransaction.from_tx(tx1) if tx1 is not None else CMutableTransaction()

# There might be a better way to fund the new outputs, so delete all but the
# first input. Of course, we can't delete all the inputs - the new transaction
# wouldn't conflict with the old one and you'd pay everyone twice!
tx2.vin = tx2.vin[0:1]

if not args.first_seen_safe and len(tx2.vout) > 0:
    # Delete the change output.
    #
    # Unfortunately there isn't any way to ask Bitcoin Core if a given address
    # is a change address; if you're sending yourself funds to test the feature
    # it's not possible to distinguish change from send-to-self outputs.
    #
    # So instead we always build transactions such that the first output is
    # change, and we delete only that output. Not pretty - you don't want to do
    # something that dumb and anti-privacy in a real wallet - but without a way
    # of keeping state this is the best we've got.
    try:
        addr = CBitcoinAddress.from_scriptPubKey(tx2.vout[0].scriptPubKey)
    except ValueError:
        pass
    else:
        # There is an edge case not handled: if we have multiple outputs but
        # didn't need a change output. But whatever, this is just a demo!
        if len(tx2.vout) > 1 and rpc.validateaddress(addr)['ismine']:
            tx2.vout = tx2.vout[1:]


# Add the new output
payment_address = CBitcoinAddress(args.address)
payment_txout = CMutableTxOut(int(args.amount * COIN), payment_address.to_scriptPubKey())
tx2.vout.append(payment_txout)

r = rpc.fundrawtransaction(tx2)
tx2 = CMutableTransaction.from_tx(r['tx'])
tx2_fee = r['fee']

# Set nSequnce on all inputs appropriately to opt-in to full-RBF
for txin in tx2.vin:
    txin.nSequence = 0

# Move change txout to 0th slot
changepos = r['changepos']
if changepos >= 0:
    tx2.vout = tx2.vout[changepos:changepos + 1] + tx2.vout[0:changepos] + tx2.vout[changepos + 1:]

else:
    # TODO: handle case where a replacement tx doesn't need a change output.
    assert tx1 is None


r = rpc.signrawtransaction(tx2)
assert(r['complete'])
tx2 = CMutableTransaction.from_tx(r['tx'])

if tx1 is not None:
    tx2_size = len(tx2.serialize())

    tx1_size = len(tx1.serialize())
    tx1_value_in = 0
    for txin in tx1.vin:
        r = rpc.gettxout(txin.prevout, includemempool=False)
        tx1_value_in += r['txout'].nValue

    tx1_fee = tx1_value_in - sum(txout.nValue for txout in tx1.vout)

    # It's possible for fundrawtransaction to both reduce the feerate, and the
    # absolute fee. Both need to be > in tx2 than in tx1, adjust as necessary.
    if tx1_fee > tx2_fee:
        tx2.vout[0].nValue -= tx1_fee - tx2_fee
        tx2_fee = tx1_fee

    if tx1_fee / tx1_size > tx2_fee / tx2_size:
        d = int(tx1_fee * (tx2_size / tx1_size) - tx2_fee)
        tx2.vout[0].nValue -= d
        tx2_fee += d

    # Pay for the relay bandwidth consumed by the replacement.
    #
    # fundrawtransaction can't take this into account, so just calculate that delta
    # and reduce the change output by it.
    #
    # Unfortunately fundrawtransaction returns empty scriptSigs, so we have to
    # do this after signing to know how big the transaction actually is.
    relay_bw_fee = int(tx2_size/1000 * args.relay_bw_feerate*COIN)
    logging.info('Paying %s for relay bandwidth' % str_money_value(relay_bw_fee))

    # TODO: handle case where this brings nValue below the dust limit
    tx2.vout[0].nValue -= relay_bw_fee

    r = rpc.signrawtransaction(tx2)
    assert(r['complete'])
    tx2 = r['tx']

    logging.info('Old size: %.3f KB, Old fees: %s, %s BTC/KB' % \
                    (tx1_size / 1000,
                     str_money_value(tx1_fee),
                     str_money_value((tx1_fee/tx1_size) * 1000))),
    logging.info('New size: %.3f KB, New fees: %s, %s BTC/KB' % \
                    (tx2_size / 1000,
                     str_money_value(tx2_fee),
                     str_money_value((tx2_fee/tx2_size) * 1000))),


# Sanity check that tx2 replaces/conflicts with tx1
assert tx1 is None or tx1.vin[0].prevout == tx2.vin[0].prevout

if args.dryrun:
    print(b2x(tx2.serialize()))

else:
    logging.debug('Sending tx %s' % b2x(tx2.serialize()))
    txid = rpc.sendrawtransaction(tx2)
    print(b2lx(txid))

