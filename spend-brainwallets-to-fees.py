#!/usr/bin/python3
# Copyright (C) 2014 Peter Todd <pete@petertodd.org>
#
# This file is subject to the license terms in the LICENSE file found in the
# top-level directory of this distribution.

import argparse
import binascii
import bitcoin
import bitcoin.rpc
import hashlib
import logging
import time

from bitcoin.core import *
from bitcoin.core.script import *
from bitcoin.core.scripteval import *
from bitcoin.wallet import *

known_privkeys_by_scriptPubKey = {}

known_p2sh_redeemScripts = {CScript():CScript([1]),
                            CScript([1]):CScript()}

known_p2sh_scriptPubKeys = \
        {redeemScript.to_p2sh_scriptPubKey():(redeemScript,scriptSig) for redeemScript, scriptSig in known_p2sh_redeemScripts.items()}

def create_spend_to_fees_tx(outpoint, privkey):
    txin_scriptPubKey = CScript([OP_DUP, OP_HASH160, Hash160(privkey.pub), OP_EQUALVERIFY, OP_CHECKSIG])

    txin = CMutableTxIn(outpoint)
    txout = CMutableTxOut(0, CScript([OP_RETURN]))
    tx = CMutableTransaction([txin],[txout])

    sigflags = SIGHASH_NONE | SIGHASH_ANYONECANPAY
    sighash = SignatureHash(txin_scriptPubKey, tx, 0, sigflags)
    sig = privkey.sign(sighash) + bytes([sigflags])

    txin.scriptSig = CScript([sig, privkey.pub])

    VerifyScript(txin.scriptSig, txin_scriptPubKey, tx, 0, (SCRIPT_VERIFY_P2SH,))

    return tx

def create_p2sh_spend_to_fees_tx(outpoint, scriptSig, redeemScript):
    return CTransaction([CTxIn(outpoint, scriptSig + redeemScript)],
                        [CTxOut(0, CScript([OP_RETURN]))])

def scan_tx_for_spendable_outputs(tx, txid):
    for (n, txout) in enumerate(tx.vout):
        if txout.scriptPubKey in known_privkeys_by_scriptPubKey:
            privkey = known_privkeys_by_scriptPubKey[txout.scriptPubKey]

            outpoint = COutPoint(txid, n)
            yield create_spend_to_fees_tx(outpoint, privkey)

        elif txout.scriptPubKey in known_p2sh_scriptPubKeys:
            outpoint = COutPoint(txid, n)
            redeemScript, scriptSig = known_p2sh_scriptPubKeys[txout.scriptPubKey]
            yield create_p2sh_spend_to_fees_tx(outpoint, scriptSig, redeemScript)

parser = argparse.ArgumentParser(description="Spend known secret-key outputs to fees. (e.g. brainwallets)")
parser.add_argument('-v', action='store_true',
                    dest='verbose',
                    help='Verbose')
parser.add_argument('-t', action='store_true',
                    dest='testnet',
                    help='Enable testnet')
parser.add_argument('-d', action='store', type=float,
                    dest='delay',
                    default=10,
                    help='Delay between mempool scans')
parser.add_argument('-f', action='store', type=str,
                    dest='privkey_file',
                    default='known-privkeys',
                    help='File of known privkeys and passphrases, one per line')
args = parser.parse_args()

logging.root.setLevel('INFO')
if args.verbose:
    logging.root.setLevel('DEBUG')

if args.testnet:
    bitcoin.SelectParams('testnet')

rpc = bitcoin.rpc.Proxy()

with open(args.privkey_file,'rb') as fd:
    def add_privkey(known_privkey):
        h = Hash160(known_privkey.pub)
        scriptPubKey = CScript([OP_DUP, OP_HASH160, h, OP_EQUALVERIFY, OP_CHECKSIG])
        known_privkeys_by_scriptPubKey[scriptPubKey] = known_privkey

        logging.info('Known: %s %s' % (b2x(scriptPubKey), b2x(known_privkey.pub)))

    n = 0
    for l in fd.readlines():
        n += 1

        l = l.strip()

        try:
            privkey = CBitcoinSecret(l.decode('utf8'))
            add_privkey(privkey)
        except bitcoin.base58.Base58ChecksumError:
            pass
        except bitcoin.base58.InvalidBase58Error:
            pass

        secret = hashlib.sha256(l).digest()
        add_privkey(CBitcoinSecret.from_secret_bytes(secret, False))
        add_privkey(CBitcoinSecret.from_secret_bytes(secret, True))

    logging.info('Added %d known privkeys/passphrases' % n)

known_txids = set()

while True:
    mempool_txids = set(rpc.getrawmempool())
    new_txids = mempool_txids.difference(known_txids)
    known_txids.update(mempool_txids)

    burn_txs = []
    for new_txid in new_txids:
        try:
            new_tx = rpc.getrawtransaction(new_txid)
        except IndexError:
            continue

        # The scriptSigs might not sign vout, in which case we can replace the
        # whole thing with OP_RETURN.
        if not (len(new_tx.vout) == 1
                and new_tx.vout[0].nValue == 0
                and new_tx.vout[0].scriptPubKey == CScript([OP_RETURN])):

            to_fees_tx = CTransaction(new_tx.vin,
                                      [CTxOut(0, CScript([OP_RETURN]))],
                                      nLockTime=new_tx.nLockTime,
                                      nVersion=new_tx.nVersion)

            try:
                to_fees_txid = rpc.sendrawtransaction(to_fees_tx, True)
                logging.info('Replaced tx %s with all-to-fees %s' % (b2lx(new_txid), b2lx(to_fees_txid)))

            except bitcoin.rpc.JSONRPCException as exp:
                # Couldn't replace; try spending individual outputs instead.
                burn_txs.extend(scan_tx_for_spendable_outputs(new_tx, new_txid))

    for burn_tx in burn_txs:
        try:
            txid = rpc.sendrawtransaction(burn_tx, True)
            logging.info('Sent burn tx %s' % b2lx(txid))
        except bitcoin.rpc.JSONRPCException as err:
            logging.info('Got error %s while sending %s' % (err, b2x(burn_tx.serialize())))

    logging.debug('Sleeping %f seconds' % args.delay)
    time.sleep(args.delay)
