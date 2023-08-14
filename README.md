Replace-by-Fee Tools
====================

Tools to test out RBF functionality.

Requirements: Python3 and python-bitcoinlib v0.12

To test out full-rbf functionality, the full-rbf-peering patch may be useful:
https://github.com/petertodd/bitcoin/tree/full-rbf-v25.0


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
