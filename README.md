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

    ./double-spend.py <address> <amount>

Creates two transactions in succession. The first pays the specified amount to
the specified address. The second double-spends that transaction with a
transaction with higher fees, paying only the change address. In addition you
can optionally specify that the first transaction additional OP-RETURN,
multisig, and "blacklisted" address outputs. Some miners won't accept
transactions with these output types; those miners will accept the second
double-spend transaction, helping you achieve a succesful double-spend.
