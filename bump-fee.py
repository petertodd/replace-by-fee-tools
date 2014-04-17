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

from bitcoin.core import b2x, b2lx, lx, str_money_value, COIN, CTxIn
from bitcoin.wallet import CBitcoinAddress

DUST = int(0.0001 * COIN)

parser = argparse.ArgumentParser(description="Bump tx fee.")
parser.add_argument('-v', action='store_true',
                    dest='verbose',
                    help='Verbose')
parser.add_argument('-t', action='store_true',
                    dest='testnet',
                    help='Enable testnet')
parser.add_argument('-n', action='store_true',
                    dest='dryrun',
                    help="Dry-run; don't actually send the transaction")
parser.add_argument('-r', action='store', type=float,
                    dest='ratio',
                    metavar='RATIO',
                    default=10.0,
                    help='Ratio of new fee to old fee; default 10x higher')
parser.add_argument('txid', action='store', type=str,
                    help='Transaction id')
args = parser.parse_args()

if args.verbose:
    logging.root.setLevel('DEBUG')

if args.testnet:
    bitcoin.SelectParams('testnet')

rpc = bitcoin.rpc.Proxy()

try:
    args.txid = lx(args.txid)
except ValueError as err:
    parser.error('Invalid txid: %s' % str(err))

if len(args.txid) != 32:
    parser.error('Invalid txid: Wrong length.')

try:
    rpc.gettransaction(args.txid)
except IndexError as err:
    parser.exit('Invalid txid: Not in wallet.')

txinfo = rpc.getrawtransaction(args.txid, True)
tx = txinfo['tx']

if 'confirmations' in txinfo and txinfo['confirmations'] > 0:
    parser.exit("Transaction already mined; %d confirmations." % txinfo['confirmations'])

# Find a txout that was being used for change
change_txout = None
for vout in tx.vout:
    try:
        addr = CBitcoinAddress.from_scriptPubKey(vout.scriptPubKey)
    except ValueError:
        continue

    if rpc.validateaddress(addr)['ismine']:
        change_txout = vout
        break

if change_txout is None:
    # No suitable change txout; no txout was an address in our wallet.
    #
    # Create a new txout for use as change.
    addr = rpc.getrawchangeaddress()
    change_txout = CTxOut(0, addr.to_scriptPubKey())
    tx.vout.append(change_txout)


# Find total value in
value_in = 0
for vin in tx.vin:
    prevout_tx = rpc.getrawtransaction(vin.prevout.hash)
    value_in += prevout_tx.vout[vin.prevout.n].nValue

value_out = sum([vout.nValue for vout in tx.vout])

# Units: satoshi's per byte
old_fees_per_byte = (value_in-value_out) / len(tx.serialize())
desired_fees_per_byte = old_fees_per_byte * args.ratio

logging.debug('Old size: %.3f KB, Old fees: %s, %s BTC/KB, Desired fees: %s BTC/KB' % \
        (len(tx.serialize()) / 1000,
         str_money_value(value_in-value_out),
         str_money_value(old_fees_per_byte * 1000),
         str_money_value(desired_fees_per_byte * 1000)))

unspent = sorted(rpc.listunspent(1), key=lambda x: x['amount'])

# Modify the transaction by either reducing the amount of change out, or adding
# new inputs, until it meets the fees-per-byte that we want.
while (value_in-value_out) / len(tx.serialize()) < desired_fees_per_byte:

    # What's the delta fee that we need to get to our desired fees per byte at
    # the current tx size?
    delta_fee = math.ceil((desired_fees_per_byte * len(tx.serialize())) - (value_in - value_out))

    # Ensure termination; the loop converges so it can get stuck at no fee.
    if delta_fee < 1:
        break

    logging.debug('Delta fee: %s' % str_money_value(delta_fee))

    # If we simply subtract that from the change output are we still above the
    # dust threshold?
    if change_txout.nValue - delta_fee > DUST:
        change_txout.nValue -= delta_fee
        value_out -= delta_fee

    else:
        # Looks like we need to add another input. We could be clever about
        # this, but nah, just add the largest unspent input to the tx and try
        # again.
        try:
            new_unspent = unspent[-1]
            unspent = unspent[:-1]
        except IndexError:
            parser.exit('Not enough confirmed funds left unspent to bump fees')

        new_outpoint = new_unspent['outpoint']
        new_amount = new_unspent['amount']

        logging.debug('Adding new input %s:%d with value %s BTC' % \
                (b2lx(new_outpoint.hash), new_outpoint.n,
                 str_money_value(new_amount)))

        new_txin = CTxIn(new_outpoint)
        value_in += new_amount

        change_txout.nValue += new_amount
        value_out += new_amount

        tx.vin.append(new_txin)

        # re-sign the tx so we can figure out how large the new input's scriptSig will be.
        r = rpc.signrawtransaction(tx)
        assert(r['complete'])

        tx.vin[-1].scriptSig = r['tx'].vin[-1].scriptSig


logging.debug('New size: %.3f KB, New fees: %s, %s BTC/KB' % \
        (len(tx.serialize()) / 1000,
         str_money_value(value_in-value_out),
         str_money_value((value_in-value_out) / len(tx.serialize()) * 1000)))

r = rpc.signrawtransaction(tx)
assert(r['complete'])
tx = r['tx']


if args.dryrun:
    print(b2x(tx.serialize()))

else:
    logging.debug('Sending tx %s' % b2x(tx.serialize()))
    txid = rpc.sendrawtransaction(tx)
    print(b2lx(txid))
