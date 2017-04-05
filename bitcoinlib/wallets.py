# -*- coding: utf-8 -*-
#
#    BitcoinLib - Python Cryptocurrency Library
#    WALLETS - HD wallet Class for key and transaction management
#    © 2017 April - 1200 Web Development <http://1200wd.com/>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import numbers
from sqlalchemy import or_
from bitcoinlib.db import *
from bitcoinlib.keys import HDKey, check_network_and_key
from bitcoinlib.networks import Network, DEFAULT_NETWORK
from bitcoinlib.encoding import to_hexstring
from bitcoinlib.services.services import Service
from bitcoinlib.transactions import Transaction
from bitcoinlib.mnemonic import Mnemonic

_logger = logging.getLogger(__name__)


class WalletError(Exception):
    def __init__(self, msg=''):
        self.msg = msg
        _logger.error(msg)

    def __str__(self):
        return self.msg


def list_wallets(databasefile=DEFAULT_DATABASE):
    """
    List Wallets from database
    
    :param databasefile: Location of Sqlite database (optional)
    :type databasefile: str
    :return dict: Dictionary of wallets defined in database
    """
    session = DbInit(databasefile=databasefile).session
    wallets = session.query(DbWallet).all()
    wlst = []
    for w in wallets:
        wlst.append({
            'id': w.id,
            'name': w.name,
            'owner': w.owner,
            'network': w.network_name,
            'purpose': w.purpose,
            'balance': w.balance,
        })
    session.close()
    return wlst


def wallet_exists(wallet, databasefile=DEFAULT_DATABASE):
    """
    Check if Wallets is defined in database
    :param wallet: Wallet ID as integer or Wallet Name as string
    :param databasefile: Location of Sqlite database (optional)
    :return: True or False
    """
    if wallet in [x['name'] for x in list_wallets(databasefile)]:
        return True
    if isinstance(wallet, int) and wallet in [x['id'] for x in list_wallets(databasefile)]:
        return True
    return False


def delete_wallet(wallet, databasefile=DEFAULT_DATABASE, force=False):
    """
    Delete wallet and associated keys from the database. If wallet has unspent outputs it raises a WalletError exception
    unless 'force=True' is specified
    :param wallet: Wallet ID as integer or Wallet Name as string
    :param databasefile: Location of Sqlite database (optional)
    :param force: If set to True wallet will be deleted even if unspent outputs are found. Default is False
    :return: Number of rows deleted, so integer 1 if succesfull
    """
    session = DbInit(databasefile=databasefile).session
    if isinstance(wallet, int) or wallet.isdigit():
        w = session.query(DbWallet).filter_by(id=wallet)
    else:
        w = session.query(DbWallet).filter_by(name=wallet)
    if not w or not w.first():
        raise WalletError("Wallet '%s' not found" % wallet)
    # Delete keys from this wallet
    ks = session.query(DbKey).filter_by(wallet_id=w.first().id)
    for k in ks:
        if not force and k.balance:
            raise WalletError("Key %d (%s) still has unspent outputs. Use 'force=True' to delete this wallet" %
                              (k.id, k.address))
    ks.delete()

    # TODO: Mark transactions from this wallet as watch_only
    res = w.delete()
    session.commit()
    session.close()
    _logger.info("Wallet '%s' deleted" % wallet)
    return res


def normalize_path(path):
    """ Normalize BIP0044 key path for HD keys. Using single quotes for hardened keys 

    :param path: BIP0044 key path 
    :type path: str
    :return str: Normalized BIP004 key path with single quotes
    """
    levels = path.split("/")
    npath = ""
    for level in levels:
        if not level:
            raise WalletError("Could not parse path. Index is empty.")
        nlevel = level
        if level[-1] in "'HhPp":
            nlevel = level[:-1] + "'"
        npath += nlevel + "/"
    if npath[-1] == "/":
        npath = npath[:-1]
    return npath


def parse_bip44_path(path):
    """
    Assumes a correct BIP0044 path and returns a dictionary with path items. See Bitcoin improvement proposals
    BIP0043 and BIP0044.
    
    :param path: BIP0044 path as string, with backslash (/) seperator. 
    Specify path in this format: m / purpose' / cointype' / account' / change / address_index
    Path lenght must be between 1 and 6 (Depth between 0 and 5)
    :return: Dictionary with path items: isprivate, purpose, cointype, account, change and address_index
    """

    pathl = normalize_path(path).split('/')
    if not 0 < len(pathl) <= 6:
        raise WalletError("Not a valid BIP0044 path. Path length (depth) must be between 1 and 6 not %d" % len(pathl))
    return {
        'isprivate': True if pathl[0] == 'm' else False,
        'purpose': '' if len(pathl) < 2 else pathl[1],
        'cointype': '' if len(pathl) < 3 else pathl[2],
        'account': '' if len(pathl) < 4 else pathl[3],
        'change': '' if len(pathl) < 5 else pathl[4],
        'address_index': '' if len(pathl) < 6 else pathl[5],
    }


class HDWalletKey:
    """
    Normally only used as attribute of HDWallet class. Contains HDKey object and extra information such as path and
    balance.
    """

    @staticmethod
    def from_key(name, wallet_id, session, key='', hdkey_object=None, account_id=0, network=None, change=0,
                 purpose=44, parent_id=0, path='m'):
        """
        Create HDWalletKey from a HDKey object or key
        :param name: 
        :param wallet_id: 
        :param session: Sqlalchemy Session object
        :param key: 
        :param hdkey_object: 
        :param account_id: 
        :param network: 
        :param change: 
        :param purpose: 
        :param parent_id: 
        :param path: 
        :return: HDWalletKey object
        """
        if not hdkey_object:
            if network is None:
                network = DEFAULT_NETWORK
            k = HDKey(import_key=key, network=network)
        else:
            k = hdkey_object

        keyexists = session.query(DbKey).filter(DbKey.key_wif == k.extended_wif()).first()
        if keyexists:
            _logger.warning("Key %s already exists" % (key or k.extended_wif()))
            return HDWalletKey(keyexists.id, session, k)

        if k.depth != len(path.split('/'))-1:
            if path == 'm' and k.depth == 3:
                # Create path when importing new account-key
                nw = Network(network)
                networkcode = nw.bip44_cointype
                path = "m/%d'/%s'/%d'" % (purpose, networkcode, account_id)
            else:
                raise WalletError("Key depth of %d does not match path lenght of %d for path %s" %
                                  (k.depth, len(path.split('/')) - 1, path))

        wk = session.query(DbKey).filter(or_(DbKey.key == k.key_hex,
                                             DbKey.key_wif == k.extended_wif())).first()
        if wk:
            return HDWalletKey(wk.id, session, k)

        nk = DbKey(name=name, wallet_id=wallet_id, key=k.key_hex, purpose=purpose,
                   account_id=account_id, depth=k.depth, change=change, address_index=k.child_index,
                   key_wif=k.extended_wif(), address=k.key.address(), parent_id=parent_id,
                   is_private=True, path=path, key_type=k.key_type)
        session.add(nk)
        session.commit()
        return HDWalletKey(nk.id, session, k)

    @classmethod
    def from_key_object(cls, hdkey_object, name, wallet_id, session, account_id=0, network='bitcoin', change=0,
                        purpose=44, parent_id=0, path='m'):
        if not isinstance(hdkey_object, HDKey):
            raise WalletError("The hdkey_object variable must be a HDKey type")
        return cls.from_key(name=name, wallet_id=wallet_id, session=session,
                            hdkey_object=hdkey_object, account_id=account_id, network=network,
                            change=change, purpose=purpose, parent_id=parent_id, path=path)

    def __init__(self, key_id, session, hdkey_object=None):
        wk = session.query(DbKey).filter_by(id=key_id).first()
        if wk:
            self._dbkey = wk
            self._hdkey_object = hdkey_object
            self.key_id = key_id
            self.name = wk.name
            self.wallet_id = wk.wallet_id
            self.key_hex = wk.key
            self.account_id = wk.account_id
            self.change = wk.change
            self.address_index = wk.address_index
            self.key_wif = wk.key_wif
            self.address = wk.address
            self._balance = wk.balance
            self.purpose = wk.purpose
            self.parent_id = wk.parent_id
            self.is_private = wk.is_private
            self.path = wk.path
            self.wallet = wk.wallet
            self.network = Network(wk.wallet.network_name)

            self.depth = wk.depth
            self.key_type = wk.key_type
        else:
            raise WalletError("Key with id %s cdnot found" % key_id)

    def key(self):
        if self._hdkey_object is None:
            self._hdkey_object = HDKey(import_key=self.key_wif, network=self.network.network_name)
        return self._hdkey_object

    def balance(self, fmt=''):
        if fmt == 'string':
            return self.network.print_value(self._balance)
        else:
            return self._balance

    def fullpath(self, change=None, address_index=None, max_depth=5):
        # BIP43 + BIP44: m / purpose' / coin_type' / account' / change / address_index
        if change is None:
            change = self.change
        if address_index is None:
            address_index = self.address_index
        if self.key_hex:
            p = ["m"]
        else:
            p = ["M"]
        p.append(str(self.purpose) + "'")
        p.append(str(self.network.bip44_cointype) + "'")
        p.append(str(self.account_id) + "'")
        p.append(str(change))
        p.append(str(address_index))
        return p[:max_depth]

    def parent(self, session):
        return HDWalletKey(self.parent_id, session=session, hdkey_object=self.key())

    def updatebalance(self):
        self._balance = Service(network=self.network.network_name).getbalance([self.address])
        self._dbkey.balance = self._balance

    def updateutxo(self):
        utxos = Service(network=self.network.network_name).getutxos([self.address])
        from pprint import pprint
        pprint(utxos)

    def info(self):
        print("--- Key ---")
        print(" ID                             %s" % self.key_id)
        print(" Key Type                       %s" % self.key_type)
        print(" Is Private                     %s" % self.is_private)
        print(" Name                           %s" % self.name)
        print(" Key Hex                        %s" % self.key_hex)
        print(" Key WIF                        %s" % self.key_wif)
        print(" Account ID                     %s" % self.account_id)
        print(" Parent ID                      %s" % self.parent_id)
        print(" Depth                          %s" % self.depth)
        print(" Change                         %s" % self.change)
        print(" Address Index                  %s" % self.address_index)
        print(" Address                        %s" % self.address)
        print(" Path                           %s" % self.path)
        print(" Balance                        %s" % self.balance(fmt='string'))
        print("\n")


class HDWallet:

    @classmethod
    def create(cls, name, key='', owner='', network=None, account_id=0, purpose=44,
               databasefile=DEFAULT_DATABASE):
        session = DbInit(databasefile=databasefile).session
        if session.query(DbWallet).filter_by(name=name).count():
            raise WalletError("Wallet with name '%s' already exists" % name)
        else:
            _logger.info("Create new wallet '%s'" % name)
        if key:
            network = check_network_and_key(key, network)
        elif network is None:
            network = DEFAULT_NETWORK
        new_wallet = DbWallet(name=name, owner=owner, network_name=network, purpose=purpose)
        session.add(new_wallet)
        session.commit()
        new_wallet_id = new_wallet.id

        mk = HDWalletKey.from_key(key=key, name=name, session=session, wallet_id=new_wallet_id, network=network,
                                  account_id=account_id, purpose=purpose)
        if mk.depth > 4:
            raise WalletError("Cannot create new wallet with main key of depth 5 or more")
        new_wallet.main_key_id = mk.key_id
        session.commit()
        session.close()

        w = HDWallet(new_wallet_id, databasefile=databasefile, main_key_object=mk.key())
        if mk.depth == 0:
            nw = Network(network)
            networkcode = nw.bip44_cointype
            path = ["%d'" % purpose, "%s'" % networkcode]
            w._create_keys_from_path(mk, path, name=name, wallet_id=new_wallet_id, network=network, session=session,
                                     account_id=account_id, purpose=purpose, basepath="m")
            w.new_account(account_id=account_id)
        return w

    def _create_keys_from_path(self, parent, path, wallet_id, account_id, network, session,
                               name='', basepath='', change=0, purpose=44):
        # Initial checks and settings
        parent_id = 0
        nk = parent
        ck = nk.key()
        if not isinstance(path, list):
            raise WalletError("Path must be of type 'list'")
        if len(basepath) and basepath[-1] != "/":
            basepath += "/"

        # Check for closest ancestor in wallet
        spath = basepath + '/'.join(path)
        rkey = None
        while spath and not rkey:
            rkey = self._session.query(DbKey).filter_by(wallet_id=wallet_id, path=spath).first()
            spath = '/'.join(spath.split("/")[:-1])
        if rkey is not None and rkey.path not in [basepath, basepath[:-1]]:
            path = (basepath + '/'.join(path)).replace(rkey.path + '/', '').split('/')
            basepath = rkey.path + '/'
            nk = self.key(rkey.id)
            ck = nk.key()

        # Create new keys from path
        for l in range(len(path)):
            pp = "/".join(path[:l+1])
            fullpath = basepath + pp
            ck = ck.subkey_for_path(path[l])
            nk = HDWalletKey.from_key_object(ck, name=name, wallet_id=wallet_id, network=network,
                                             account_id=account_id, change=change, purpose=purpose, path=fullpath,
                                             parent_id=parent_id, session=session)
            self._key_objects.update({nk.key_id: nk})
            parent_id = nk.key_id
        _logger.info("New key(s) created for parent_id %d" % parent_id)
        return nk

    def __enter__(self):
        return self

    def __init__(self, wallet, databasefile=DEFAULT_DATABASE, main_key_object=None):
        self._session = DbInit(databasefile=databasefile).session
        if isinstance(wallet, int) or wallet.isdigit():
            w = self._session.query(DbWallet).filter_by(id=wallet).scalar()
        else:
            w = self._session.query(DbWallet).filter_by(name=wallet).scalar()
        if w:
            self._dbwallet = w
            self.wallet_id = w.id
            self._name = w.name
            self._owner = w.owner
            self.network = Network(w.network_name)
            self.purpose = w.purpose
            self._balance = w.balance
            self.main_key_id = w.main_key_id
            if main_key_object:
                self.main_key = HDWalletKey(self.main_key_id, session=self._session, hdkey_object=main_key_object)
            else:
                self.main_key = HDWalletKey(self.main_key_id, session=self._session)
            self.default_account_id = 0
            _logger.info("Opening wallet '%s'" % self.name)
            self._key_objects = {
                self.main_key_id: self.main_key
            }
        else:
            raise WalletError("Wallet '%s' not found, please specify correct wallet ID or name." % wallet)

    def __exit__(self, exception_type, exception_value, traceback):
        self._session.close()

    # def _hdwalletkey_from_key(self, name, wallet_id, session, key='', hdkey_object=None, account_id=0, network=None, change=0,
    #              purpose=44, parent_id=0, path='m'):

    # def __del__(self):
    #     if self._session is not None:
    #         pprint(self._session)
    #         try:
    #             self._session.close()
    #         except:
    #             import pdb; pdb.set_trace()

    def balance(self, fmt=''):
        if fmt == 'string':
            return self.network.print_value(self._balance)
        else:
            return self._balance

    @property
    def owner(self):
        return self._owner

    @owner.setter
    def owner(self, value):
        self._owner = value
        self._dbwallet.owner = value
        self._session.commit()

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if wallet_exists(value):
            raise WalletError("Wallet with name '%s' already exists" % value)
        self._name = value
        self._dbwallet.name = value
        self._session.commit()

    def import_key(self, key, account_id=None):
        return HDWalletKey.from_key(
            key=key, name=self.name, wallet_id=self.wallet_id, network=self.network.network_name,
            account_id=account_id, purpose=self.purpose, session=self._session)

    def import_hdkey_object(self, hdkey_object, account_id=None):
        return HDWalletKey.from_key_object(
            hdkey_object, name=self.name, wallet_id=self.wallet_id, network=self.network.network_name,
            account_id=account_id, purpose=self.purpose, session=self._session)

    def new_key(self, name='', account_id=None, change=0, max_depth=5):
        if account_id is None:
            account_id = self.default_account_id

        # Get account key, create one if it doesn't exist
        acckey = self._session.query(DbKey). \
            filter_by(wallet_id=self.wallet_id, purpose=self.purpose, account_id=account_id, depth=3).scalar()
        if not acckey:
            hk = self.new_account(account_id=account_id)
            if hk:
                acckey = hk._dbkey
        if not acckey:
            raise WalletError("No key found this wallet_id, network and purpose. "
                              "Is there a BIP32 Master key imported?")
        else:
            main_acc_key = self.key(acckey.id)

        # Determine new key ID
        prevkey = self._session.query(DbKey). \
            filter_by(wallet_id=self.wallet_id, purpose=self.purpose,
                      account_id=account_id, change=change, depth=max_depth). \
            order_by(DbKey.address_index.desc()).first()
        address_index = 0
        if prevkey:
            address_index = prevkey.address_index + 1

        # Compose key path and create new key
        newpath = [(str(change)), str(address_index)]
        bpath = main_acc_key.path + '/'
        # pathdepth = max_depth - self.main_key.depth
        if not name:
            name = "Key %d" % address_index
        newkey = self._create_keys_from_path(
            main_acc_key, newpath, name=name, wallet_id=self.wallet_id,  account_id=account_id,
            change=change, network=self.network.network_name, purpose=self.purpose, basepath=bpath,
            session=self._session
        )
        return newkey

    def new_key_change(self, name='', account_id=0):
        return self.new_key(name=name, account_id=account_id, change=1)

    def new_account(self, name='', account_id=None):
        # Determine account_id and name
        if account_id is None:
            last_id = self._session.query(DbKey). \
                filter_by(wallet_id=self.wallet_id, purpose=self.purpose). \
                order_by(DbKey.account_id.desc()).first().account_id
            account_id = last_id + 1
        if not name:
            name = 'Account #%d' % account_id
        if self.keys(account_id=account_id, depth=3):
            raise WalletError("Account with ID %d already exists for this wallet")

        # Get root key of new account
        accrootkey = self._session.query(DbKey).filter_by(wallet_id=self.wallet_id, purpose=self.purpose,
                                                          depth=2).scalar()
        if not accrootkey:
            raise WalletError("No key found for this wallet_id, network and purpose. Can not create new"
                              "account for Public wallets, is there a BIP32 Master key imported?")
        accrootkey_obj = self.key(accrootkey.id)

        # Create new account addresses and return main account key
        newpath = [str(account_id) + "'"]
        acckey = self._create_keys_from_path(
            accrootkey_obj, newpath, name=name, wallet_id=self.wallet_id,  account_id=account_id,
            network=self.network.network_name, purpose=self.purpose, basepath=accrootkey_obj.path, session=self._session
        )
        self._create_keys_from_path(
            acckey, ['0'], name=acckey.name + ' Payments', wallet_id=self.wallet_id, account_id=account_id,
            network=self.network.network_name, purpose=self.purpose, basepath=acckey.path, session=self._session)
        self._create_keys_from_path(
            acckey, ['1'], name=acckey.name + ' Change', wallet_id=self.wallet_id, account_id=account_id,
            network=self.network.network_name, purpose=self.purpose, basepath=acckey.path, session=self._session)
        return acckey

    def key_for_path(self, path, name='', account_id=0, change=0, disable_check=False):
        # Validate key path
        if not disable_check:
            pathdict = parse_bip44_path(path)
            purpose = 0 if not pathdict['purpose'] else int(pathdict['purpose'].replace("'", ""))
            if purpose != self.purpose:
                raise WalletError("Cannot create key with different purpose field (%d) as existing wallet (%d)" % (
                purpose, self.purpose))
            cointype = 0 if not pathdict['cointype'] else int(pathdict['cointype'].replace("'", ""))
            if cointype != self.network.bip44_cointype:
                raise WalletError("Multiple cointypes per wallet are not supported at the moment. "
                                  "Cannot create key with different cointype field (%d) as existing wallet (%d)" % (
                                  cointype, self.network.bip44_cointype))
            if (pathdict['cointype'][-1] != "'" or pathdict['purpose'][-1] != "'"
                              or pathdict['account'][-1] != "'"):
                raise WalletError("Cointype, purpose and account must be hardened, see BIP43 and BIP44 definitions")
        if not name:
            name = self.name

        # Check for closest ancestor in wallet
        spath = normalize_path(path)
        rkey = None
        while spath and not rkey:
            rkey = self._session.query(DbKey).filter_by(path=spath, wallet_id=self.wallet_id).first()
            spath = '/'.join(spath.split("/")[:-1])

        # Key already found in db, return key
        if rkey.path == path:
            return self.key(rkey.id)

        parent_key = self.main_key
        subpath = path
        basepath = ''
        if rkey is not None:
            subpath = normalize_path(path).replace(rkey.path + '/', '')
            basepath = rkey.path
            if self.main_key.key_wif != rkey.key_wif:
                parent_key = self.key(rkey.id)
        newkey = self._create_keys_from_path(
            parent_key, subpath.split("/"), name=name, wallet_id=self.wallet_id,
            account_id=account_id, change=change,
            network=self.network.network_name, purpose=self.purpose, basepath=basepath, session=self._session)
        return newkey

    def keys(self, account_id=None, name=None, key_id=None, change=None, depth=None, as_dict=False):
        qr = self._session.query(DbKey).filter_by(wallet_id=self.wallet_id, purpose=self.purpose)
        if account_id is not None:
            qr = qr.filter(DbKey.account_id == account_id)
            qr = qr.filter(DbKey.depth > 3)
        if change is not None:
            qr = qr.filter(DbKey.change == change)
            qr = qr.filter(DbKey.depth > 4)
        if depth is not None:
            qr = qr.filter(DbKey.depth == depth)
        if name is not None:
            qr = qr.filter(DbKey.name == name)
        if key_id is not None:
            qr = qr.filter(DbKey.id == key_id)
        return as_dict and [x.__dict__ for x in qr.all()] or qr.all()

    def key(self, term):
        """
        Search for wallet key in this wallet.
        
        :param term: Search term can be key ID, key address, key WIF or key name
        :return: Key as HDWalletKey object
        """
        dbkey = None
        if isinstance(term, numbers.Number):
            dbkey = self._session.query(DbKey).filter_by(id=term).scalar()
        if not dbkey:
            dbkey = self._session.query(DbKey).filter_by(address=term).first()
        if not dbkey:
            dbkey = self._session.query(DbKey).filter_by(key_wif=term).first()
        if not dbkey:
            dbkey = self._session.query(DbKey).filter_by(name=term).first()
        if dbkey:
            if dbkey.id in self._key_objects.keys():
                return self._key_objects[dbkey.id]
            else:
                return HDWalletKey(key_id=dbkey.id, session=self._session)
        else:
            raise KeyError("Key '%s' not found" % term)

    def accounts(self, account_id, as_dict=False):
        return self.keys(account_id, depth=3, as_dict=as_dict)

    def keys_addresses(self, account_id, as_dict=False):
        return self.keys(account_id, depth=5, as_dict=as_dict)

    def keys_address_payment(self, account_id, as_dict=False):
        return self.keys(account_id, depth=5, change=0, as_dict=as_dict)

    def keys_address_change(self, account_id, as_dict=False):
        return self.keys(account_id, depth=5, change=1, as_dict=as_dict)

    def addresslist(self, account_id=None, key_id=None):
        addresslist = []
        for key in self.keys(account_id=account_id, key_id=key_id):
            addresslist.append(key.address)
        return addresslist

    def updatebalance(self, account_id=None):
        self._balance = Service(network=self.network.network_name).getbalance(self.addresslist(account_id=account_id))
        self._dbwallet.balance = self._balance
        self._session.commit()

    def updateutxos(self, account_id=None, key_id=None):
        # Delete all utxo's for this account
        # TODO: This could be done more efficiently probably:
        qr = self._session.query(DbTransaction).join(DbTransaction.key).\
            filter(DbTransaction.spend is False, DbKey.account_id == account_id)
        if key_id is not None:
            qr.filter(DbTransaction.key_id == key_id)
        [self._session.delete(o) for o in qr.all()]
        self._session.commit()

        utxos = Service(network=self.network.network_name).\
            getutxos(self.addresslist(account_id=account_id, key_id=key_id))
        key_balances = {}
        count_utxos = 0
        for utxo in utxos:
            key = self._session.query(DbKey).filter_by(address=utxo['address']).scalar()
            if key.id in key_balances:
                key_balances[key.id] += int(utxo['value'])
            else:
                key_balances[key.id] = int(utxo['value'])

            # Skip if utxo was already imported
            if self._session.query(DbTransaction).filter_by(tx_hash=utxo['tx_hash']).count():
                continue

            new_utxo = DbTransaction(key_id=key.id, tx_hash=utxo['tx_hash'], confirmations=utxo['confirmations'],
                                     output_n=utxo['output_n'], index=utxo['index'], value=utxo['value'],
                                     script=utxo['script'], spend=False)
            self._session.add(new_utxo)
            count_utxos += 1

        total_balance = 0
        for kb in key_balances:
            getkey = self._session.query(DbKey).filter_by(id=kb).scalar()
            getkey.balance = key_balances[kb]
            total_balance += key_balances[kb]

        self._dbwallet.balance = total_balance
        self._balance = total_balance
        _logger.info("Got %d new UTXOs for account %s. Total balance %s" % (count_utxos, account_id, total_balance))

        self._session.commit()

    def getutxos(self, account_id, min_confirms=0):
        utxos = self._session.query(DbTransaction, DbKey.address).join(DbTransaction.key).\
            filter(DbTransaction.spend.op("IS")(False), DbKey.account_id == account_id,
                   DbTransaction.confirmations >= min_confirms).order_by(DbTransaction.confirmations.desc()).all()
        res = []
        for utxo in utxos:
            u = utxo[0].__dict__
            del u['_sa_instance_state'], u['key_id']
            u['address'] = utxo[1]
            u['value'] = int(u['value'])
            res.append(u)
        return res

    def send(self, to_address, amount, account_id=None, fee=None):
        outputs = [(to_address, amount)]
        t = self.create_transaction(outputs, account_id=account_id, fee=fee)
        srv = Service(network='testnet')
        _logger.debug("Push send transaction to network: %s" % to_hexstring(t.raw()))
        txid = srv.sendrawtransaction(to_hexstring(t.raw()))
        if not txid:
            raise WalletError("Could not send transaction: %s" % srv.errors)
        _logger.info("Succesfully pushed transaction, returned txid: %s" % txid)
        return txid

    @staticmethod
    def _select_inputs(amount, utxo_query=None):
        if not utxo_query:
            return []

        # Try to find one utxo with exact amount or higher
        one_utxo = utxo_query.filter(DbTransaction.spend.op("IS")(False), DbTransaction.value >= amount).\
            order_by(DbTransaction.value).first()
        if one_utxo:
            return [one_utxo]

        # Otherwise compose of 2 or more lesser outputs
        lessers = utxo_query.filter(DbTransaction.spend.op("IS")(False), DbTransaction.value < amount).\
            order_by(DbTransaction.value.desc()).all()
        total_amount = 0
        selected_utxos = []
        for utxo in lessers:
            if total_amount < amount:
                selected_utxos.append(utxo)
                total_amount += utxo.value
        if total_amount < amount:
            return []
        return selected_utxos

    def create_transaction(self, output_arr, input_arr=None, account_id=None, fee=None, min_confirms=4):
        amount_total_output = 0
        t = Transaction(network=self.network.network_name)
        for o in output_arr:
            amount_total_output += o[1]
            t.add_output(o[1], o[0])

        if account_id is None:
            account_id = self.default_account_id

        qr = self._session.query(DbTransaction)
        qr.join(DbTransaction.key).filter(DbTransaction.spend.op("IS")(False), DbKey.account_id == account_id)
        qr.filter(DbTransaction.confirmations >= min_confirms)
        utxos = qr.all()
        if not utxos:
            _logger.warning("Create transaction: No unspent transaction outputs found")
            return None

        # TODO: Estimate fees
        if fee is None:
            fee = int(0.0003 * pow(10, 8))

        if input_arr is None:
            input_arr = []
            amount_total_input = 0
            selected_utxos = self._select_inputs(amount_total_output + fee, qr)
            if not selected_utxos:
                raise WalletError("Not enough unspent transaction outputs found")
            for utxo in selected_utxos:
                amount_total_input += utxo.value
                input_arr.append((utxo.tx_hash, utxo.output_n, utxo.key_id))

            amount_change = int(amount_total_input - (amount_total_output + fee))
        else:
            # TODO:
            raise WalletError("This is not implemented yet")

        # Add inputs
        sign_arr = []
        for inp in input_arr:
            # TODO: Make this more efficient...
            key = self._session.query(DbKey).filter_by(id=inp[2]).scalar()
            k = HDKey(key.key_wif)
            id = t.add_input(inp[0], inp[1], public_key=k.public_byte)
            sign_arr.append((k.private_byte, id))

        # Add change output
        if amount_change:
            ck = self.new_key_change('Change', account_id)
            t.add_output(amount_change, ck.address)

        # Sign inputs
        for ti in sign_arr:
            t.sign(ti[0], ti[1])

        # Verify transaction
        if not t.verify():
            raise WalletError("Cannot verify transaction. Create transaction failed")

        return t

    def info(self, detail=3):
        print("=== WALLET ===")
        print(" ID                             %s" % self.wallet_id)
        print(" Name                           %s" % self.name)
        print(" Owner                          %s" % self._owner)
        print(" Network                        %s" % self.network.description)
        print(" Balance                        %s" % self.balance(fmt='string'))
        print("")

        if detail:
            print("= Main key =")
            self.main_key.info()
        if detail > 1:
            print("= Keys Overview = ")
            if detail < 3:
                ds = [0, 3, 5]
            else:
                ds = range(6)
            for d in ds:
                for key in self.keys(depth=d):
                    print("%5s %-28s %-45s %-25s %25s" % (key.id, key.path, key.address, key.name,
                                                          self.network.print_value(key.balance)))
        print("\n")


if __name__ == '__main__':
    #
    # WALLETS EXAMPLES
    #

    # First recreate database to avoid already exist errors
    import os
    from pprint import pprint
    test_databasefile = 'bitcoinlib.test.sqlite'
    test_database = DEFAULT_DATABASEDIR + test_databasefile
    if os.path.isfile(test_database):
        os.remove(test_database)

    print("\n=== Most simple way to create Bitcoin Wallet ===")
    w = HDWallet.create('MyWallet', databasefile=test_database)
    w.info()

    print("\n=== Create new Testnet Wallet and generate a some new keys ===")
    with HDWallet.create(name='Personal', network='testnet', databasefile=test_database) as wallet:
        wallet.info(detail=3)
        wallet.new_account()
        new_key1 = wallet.new_key()
        new_key2 = wallet.new_key()
        new_key3 = wallet.new_key()
        new_key4 = wallet.new_key(change=1)
        new_key5 = wallet.key_for_path("m/44'/1'/100'/1200/1200")
        new_key6a = wallet.key_for_path("m/44'/1'/100'/1200/1201")
        new_key6b = wallet.key_for_path("m/44'/1'/100'/1200/1201")
        wallet.info(detail=3)
        donations_account = wallet.new_account()
        new_key8 = wallet.new_key(account_id=donations_account.account_id)
        wallet.info(detail=3)

    print("\n=== Create new Wallet with Testnet master key and account ID 99 ===")
    testnet_wallet = HDWallet.create(
        name='TestNetWallet',
        key='tprv8ZgxMBicQKsPeWn8NtYVK5Hagad84UEPEs85EciCzf8xYWocuJovxsoNoxZAgfSrCp2xa6DdhDrzYVE8UXF75r2dKePyA'
            '7irEvBoe4aAn52',
        network='testnet',
        account_id=99,
        databasefile=test_database)
    nk = testnet_wallet.new_key(account_id=99, name="Address #1")
    nk2 = testnet_wallet.new_key(account_id=99, name="Address #2")
    nkc = testnet_wallet.new_key_change(account_id=99, name="Change #1")
    nkc2 = testnet_wallet.new_key_change(account_id=99, name="Change #2")
    testnet_wallet.updateutxos()
    testnet_wallet.info(detail=3)

    # Three ways of getting the a HDWalletKey, with ID, address and name:
    print(testnet_wallet.key(1).address)
    print(testnet_wallet.key('n3UKaXBRDhTVpkvgRH7eARZFsYE989bHjw').address)
    print(testnet_wallet.key('TestNetWallet').address)

    print("\n=== Import Account Bitcoin Testnet key with depth 3 ===")
    accountkey = 'tprv8h4wEmfC2aSckSCYa68t8MhL7F8p9xAy322B5d6ipzY5ZWGGwksJMoajMCqd73cP4EVRygPQubgJPu9duBzPn3QV' \
                 '8Y7KbKUnaMzxnnnsSvh'
    wallet_import2 = HDWallet.create(
        databasefile=test_database,
        name='Account Import',
        key=accountkey,
        network='testnet',
        account_id=99)
    wallet_import2.info(detail=3)
    del wallet_import2

    print("\n=== Create simple wallet and import some unrelated private keys ===")
    simple_wallet = HDWallet.create(
        name='Simple Wallet',
        key='L5fbTtqEKPK6zeuCBivnQ8FALMEq6ZApD7wkHZoMUsBWcktBev73',
        databasefile=test_database)
    simple_wallet.import_key('KxVjTaa4fd6gaga3YDDRDG56tn1UXdMF9fAMxehUH83PTjqk4xCs')
    simple_wallet.import_key('L3RyKcjp8kzdJ6rhGhTC5bXWEYnC2eL3b1vrZoduXMht6m9MQeHy')
    simple_wallet.updateutxos()
    simple_wallet.info(detail=3)
    del simple_wallet

    print("\n=== Create wallet with public key to generate addresses without private key ===")
    pubkey = 'tpubDDkyPBhSAx8DFYxx5aLjvKH6B6Eq2eDK1YN76x1WeijE8eVUswpibGbv8zJjD6yLDHzVcqWzSp2fWVFhEW9XnBssFqM' \
             'wt9SrsVeBeqfBbR3'
    pubwal = HDWallet.create(
        databasefile=test_database,
        name='Import Public Key Wallet',
        key=pubkey,
        network='testnet',
        account_id=0)
    newkey = pubwal.new_key()
    pubwal.info(detail=3)
    del pubwal

    print("\n=== Create Litecoin wallet ===")
    litecoin_wallet = HDWallet.create(
        databasefile=test_database,
        name='Litecoin Wallet',
        network='litecoin')
    newkey = litecoin_wallet.new_key()
    litecoin_wallet.info(detail=3)
    del litecoin_wallet

    print("\n=== Create Litecoin testnet Wallet from Mnemonic Passphrase ===")
    # words = Mnemonic('english').generate()
    words = 'blind frequent camera goddess pottery repair skull year mistake wrist lonely mix'
    print("Generated Passphrase: %s" % words)
    seed = Mnemonic().to_seed(words)
    hdkey = HDKey().from_seed(seed, network='litecoin_testnet')
    wallet = HDWallet.create(name='Mnemonic Wallet', network='litecoin_testnet',
                             key=hdkey.extended_wif(), databasefile=test_database)
    wallet.new_key("Input", 0)
    # wallet.updateutxos()  # TODO: fix for litecoin testnet
    wallet.info(detail=3)

    print("\n=== Test import Litecoin key in Bitcoin wallet (should give error) ===")
    w = HDWallet.create(
        name='Wallet Error',
        databasefile=test_database)
    try:
        w.import_key(key='T43gB4F6k1Ly3YWbMuddq13xLb56hevUDP3RthKArr7FPHjQiXpp')
    except KeyError as e:
        print("Import litecoin key in bitcoin wallet gives an error: %s" % e)

    print("\n=== Normalize BIP48 key path ===")
    key_path = "m/44h/1'/0p/2000/1"
    print("Raw: %s, Normalized: %s" % (key_path, normalize_path(key_path)))

    print("\n=== Send testbitcoins to an address ===")
    wallet_import = HDWallet('TestNetWallet', databasefile=test_database)
    wallet_import.info(detail=3)
    wallet_import.updateutxos(99)
    wallet_import.getutxos(99, 4)
    print("\n= UTXOs =")
    for utxo in wallet_import.getutxos(99):
        print("%s %s (%d confirms)" % (
        utxo['address'], wallet_import.network.print_value(utxo['value']), utxo['confirmations']))
    res = wallet_import.send('mxdLD8SAGS9fe2EeCXALDHcdTTbppMHp8N', 5000000, 99)
    # res = wallet_import.send('mwCwTceJvYV27KXBc3NJZys6CjsgsoeHmf', 5000000, 99)
    print("Send transaction result:")
    pprint(res)

    print("\n=== List wallets & delete a wallet ===")
    print(','.join([w['name'] for w in list_wallets(databasefile=test_database)]))
    hoihoi = delete_wallet(1, databasefile=test_database, force=True)
    print(','.join([w['name'] for w in list_wallets(databasefile=test_database)]))
