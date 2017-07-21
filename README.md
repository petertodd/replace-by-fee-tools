Replace-by-Fee Tools
====================

Tools to test out replace-by-fee functionality. You'll need a local node with
the replace-by-fee patch. A version applied to Bitcoin Core v0.11.0 is
available at https://github.com/petertodd/bitcoin/tree/replace-by-fee-v0.11.0

Requirements: Python3 (python-bitcoinlib included in repo as subtree)


Bump Fee
========

Basic usage:

    ./bump-fee.py <txid>

Increases the fee on a transaction by double-spending it with a second
transaction paying the original recipients. The change output will have its
value reduced to make the fee higher, and there may be additional inputs added
if the original inputs weren't enough.


Double Spend
============

Basic usage:

    ./doublespend.py <address> <amount>

Creates two transactions in succession. The first pays the specified amount to
the specified address. The second double-spends that transaction with a
transaction with higher fees, paying only the change address. In addition you
can optionally specify that the first transaction additional OP-RETURN,
multisig, and "blacklisted" address outputs. Some miners won't accept
transactions with these output types; those miners will accept the second
double-spend transaction, helping you achieve a succesful double-spend.


Incremental Send Many
=====================

Basic usage:

    ./sendmany.py <address> <amount>

Finds an unconfirmed transaction in your wallet that has opted into full-RBF
and rebroadcasts it with an additional output. If no such transaction exists, a
new opt-in full-RBF transaction is created. The first transaction input is kept
to ensure a double-spend; all other inputs are re-optimized for the new set of
outputs.  This can be significantly cheaper than respending unconfirmed outputs
in long transaction chains.

Depends on the availability of the fundrawtransaction RPC call, which is
currently only available in git master. (will be in Bitcoin Core v0.12.0)

Opt-In Full-RBF pull-req: https://github.com/bitcoin/bitcoin/pull/6871
