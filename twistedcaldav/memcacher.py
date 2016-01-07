##
# Copyright (c) 2008-2016 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

import hashlib
import cPickle
import string

from twisted.internet.defer import succeed

from twext.python.log import Logger

from twistedcaldav.memcachepool import CachePoolUserMixIn
from twistedcaldav.config import config

class Memcacher(CachePoolUserMixIn):
    log = Logger()

    MEMCACHE_KEY_LIMIT = 250      # the memcached key length limit
    NAMESPACE_MAX_LENGTH = 32     # max size of namespace we will allow
    HASH_LENGTH = 32              # length of hash we will generate
    TRUNCATED_KEY_LENGTH = MEMCACHE_KEY_LIMIT - NAMESPACE_MAX_LENGTH - HASH_LENGTH - 2  # 2 accounts for delimiters
    MEMCACHE_VALUE_LIMIT = 1024 * 1024 # the memcached default value length limit

    # Translation table: all ctrls (0x00 - 0x1F) and space and 0x7F mapped to _
    keyNormalizeTranslateTable = string.maketrans("".join([chr(i) for i in range(33)]) + chr(0x7F), "_" * 33 + "_")

    allowTestCache = False
    memoryCacheInstance = {
        True: None,
        False: None,
    }

    class memoryCacher():
        """
        A class implementing the memcache client API we care about but
        using a dict to store the results in memory. This can be used
        for caching on a single instance server, and for tests, where
        memcached may not be running.
        """

        def __init__(self, pickle=False):
            self._cache = {} # (value, expireTime, check-and-set identifier)
            self._clock = 0
            self._pickle = pickle

        def _check_key(self, key):
            if not isinstance(key, str):
                raise ValueError("memcache keys must be str type")

        def _check_value(self, value):
            if not self._pickle and not isinstance(value, str):
                raise ValueError("memcache values must be str type")

        def add(self, key, value, expireTime=0):
            self._check_key(key)
            self._check_value(value)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT or len(str(value)) > Memcacher.MEMCACHE_VALUE_LIMIT:
                return succeed(False)
            if key not in self._cache:
                if not expireTime:
                    expireTime = 99999
                self._cache[key] = (value, self._clock + expireTime, 0)
                return succeed(True)
            else:
                return succeed(False)

        def set(self, key, value, expireTime=0):
            self._check_key(key)
            self._check_value(value)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT or len(str(value)) > Memcacher.MEMCACHE_VALUE_LIMIT:
                return succeed(False)
            if not expireTime:
                expireTime = 99999
            if key in self._cache:
                identifier = self._cache[key][2]
                identifier += 1
            else:
                identifier = 0
            self._cache[key] = (value, self._clock + expireTime, identifier)
            return succeed(True)

        def checkAndSet(self, key, value, cas, flags=0, expireTime=0):
            self._check_key(key)
            self._check_value(value)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT or len(str(value)) > Memcacher.MEMCACHE_VALUE_LIMIT:
                return succeed(False)
            if not expireTime:
                expireTime = 99999
            if key in self._cache:
                identifier = self._cache[key][2]
                if cas != str(identifier):
                    return succeed(False)
                identifier += 1
            else:
                return succeed(False)
            self._cache[key] = (value, self._clock + expireTime, identifier)
            return succeed(True)

        def get(self, key, withIdentifier=False):
            self._check_key(key)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT:
                value, expires, identifier = (None, 0, "")
            else:
                value, expires, identifier = self._cache.get(key, (None, 0, ""))
                if self._clock >= expires:
                    value = None
                    identifier = ""

            if withIdentifier:
                return succeed((0, value, str(identifier)))
            else:
                return succeed((0, value,))

        def delete(self, key):
            self._check_key(key)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT:
                return succeed(False)
            try:
                del self._cache[key]
                return succeed(True)
            except KeyError:
                return succeed(False)

        def incr(self, key, delta=1):
            self._check_key(key)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT:
                return succeed(False)
            value = self._cache.get(key, None)
            if value is not None:
                value, expire, identifier = value
                try:
                    value = int(value)
                except ValueError:
                    value = None
                else:
                    value += delta
                    self._cache[key] = (str(value), expire, identifier,)
            return succeed(value)

        def decr(self, key, delta=1):
            self._check_key(key)

            if len(key) > Memcacher.MEMCACHE_KEY_LIMIT:
                return succeed(False)
            value = self._cache.get(key, None)
            if value is not None:
                value, expire, identifier = value
                try:
                    value = int(value)
                except ValueError:
                    value = None
                else:
                    value -= delta
                    if value < 0:
                        value = 0
                    self._cache[key] = (str(value), expire, identifier,)
            return succeed(value)

        def flushAll(self):
            self._cache = {}
            return succeed(True)

        def advanceClock(self, seconds):
            self._clock += seconds


    # TODO: an sqlite based cacher that can be used for multiple instance servers
    # in the absence of memcached. This is not ideal and we may want to not implement
    # this, but it is being documented for completeness.
    #
    # For now we implement a cacher that does not cache.
    class nullCacher():
        """
        A class implementing the memcache client API we care about but
        does not actually cache anything.
        """

        def add(self, key, value, expireTime=0):
            return succeed(True)

        def set(self, key, value, expireTime=0):
            return succeed(True)

        def checkAndSet(self, key, value, cas, flags=0, expireTime=0):
            return succeed(True)

        def get(self, key, withIdentifier=False):
            return succeed((0, None,))

        def delete(self, key):
            return succeed(True)

        def incr(self, key, delta=1):
            return succeed(None)

        def decr(self, key, delta=1):
            return succeed(None)

        def flushAll(self):
            return succeed(True)


    def __init__(self, namespace, pickle=False, no_invalidation=False, key_normalization=True):
        """
        @param namespace: a unique namespace for this cache's keys
        @type namespace: C{str}
        @param pickle: if C{True} values will be pickled/unpickled when stored/read from the cache,
            if C{False} values will be stored directly (and therefore must be strings)
        @type pickle: C{bool}
        @param no_invalidation: if C{True} the cache is static - there will be no invalidations. This allows
            Memcacher to use the memoryCacher cache instead of nullCacher for the multi-instance case when memcached
            is not present,as there is no issue with caches in each instance getting out of sync. If C{False} the
            nullCacher will be used for the multi-instance case when memcached is not configured.
        @type no_invalidation: C{bool}
        @param key_normalization: if C{True} the key is assumed to possibly be longer than the Memcache key size and so additional
            work is done to truncate and append a hash.
        @type key_normalization: C{bool}
        """

        assert len(namespace) <= Memcacher.NAMESPACE_MAX_LENGTH, "Memcacher namespace must be less than or equal to %s characters long" % (Memcacher.NAMESPACE_MAX_LENGTH,)
        self._memcacheProtocol = None
        self._cachePoolHandle = namespace
        self._namespace = namespace
        self._pickle = pickle
        self._noInvalidation = no_invalidation
        self._key_normalization = key_normalization


    def _getMemcacheProtocol(self):
        if self._memcacheProtocol is not None:
            return self._memcacheProtocol

        if config.Memcached.Pools.Default.ClientEnabled:
            self._memcacheProtocol = self.getCachePool()

        elif config.ProcessType == "Single" or self._noInvalidation or self.allowTestCache:
            # The memory cacher handles python types natively, but we need to treat non-str types as an error
            # if pickling is off, so we use two global memory cachers for each pickle state
            if Memcacher.memoryCacheInstance[self._pickle] is None:
                Memcacher.memoryCacheInstance[self._pickle] = Memcacher.memoryCacher(self._pickle)
            self._memcacheProtocol = Memcacher.memoryCacheInstance[self._pickle]

        else:
            # NB no need to pickle the null cacher as it handles python types natively
            self._memcacheProtocol = Memcacher.nullCacher()
            self._pickle = False

        return self._memcacheProtocol


    def _normalizeKey(self, key):

        if isinstance(key, unicode):
            key = key.encode("utf-8")
        assert isinstance(key, str), "Key must be a str."

        if self._key_normalization:
            hash = hashlib.md5(key).hexdigest()
            key = key[:Memcacher.TRUNCATED_KEY_LENGTH]
            return "%s-%s" % (key.translate(Memcacher.keyNormalizeTranslateTable), hash,)
        else:
            return key


    def add(self, key, value, expireTime=0):

        proto = self._getMemcacheProtocol()

        my_value = value
        if self._pickle:
            my_value = cPickle.dumps(value)
        self.log.debug("Adding Cache Token for {k!r}", k=key)
        return proto.add('%s:%s' % (self._namespace, self._normalizeKey(key)), my_value, expireTime=expireTime)


    def set(self, key, value, expireTime=0):

        proto = self._getMemcacheProtocol()

        my_value = value
        if self._pickle:
            my_value = cPickle.dumps(value)
        self.log.debug("Setting Cache Token for {k!r}", k=key)
        return proto.set('%s:%s' % (self._namespace, self._normalizeKey(key)), my_value, expireTime=expireTime)


    def checkAndSet(self, key, value, cas, flags=0, expireTime=0):

        proto = self._getMemcacheProtocol()

        my_value = value
        if self._pickle:
            my_value = cPickle.dumps(value)
        self.log.debug("Setting Cache Token for {k!r}", k=key)
        return proto.checkAndSet('%s:%s' % (self._namespace, self._normalizeKey(key)), my_value, cas, expireTime=expireTime)


    def get(self, key, withIdentifier=False):
        def _gotit(result, withIdentifier):
            if withIdentifier:
                _ignore_flags, identifier, value = result
            else:
                _ignore_flags, value = result
            if self._pickle and value is not None:
                value = cPickle.loads(value)
            if withIdentifier:
                value = (identifier, value)
            return value

        self.log.debug("Getting Cache Token for {k!r}", k=key)
        d = self._getMemcacheProtocol().get('%s:%s' % (self._namespace, self._normalizeKey(key)), withIdentifier=withIdentifier)
        d.addCallback(_gotit, withIdentifier)
        return d


    def delete(self, key):
        self.log.debug("Deleting Cache Token for {k!r}", k=key)
        return self._getMemcacheProtocol().delete('%s:%s' % (self._namespace, self._normalizeKey(key)))


    def incr(self, key, delta=1):
        self.log.debug("Incrementing Cache Token for {k!r}", k=key)
        return self._getMemcacheProtocol().incr('%s:%s' % (self._namespace, self._normalizeKey(key)), delta)


    def decr(self, key, delta=1):
        self.log.debug("Decrementing Cache Token for {k!r}", k=key)
        return self._getMemcacheProtocol().incr('%s:%s' % (self._namespace, self._normalizeKey(key)), delta)


    def flushAll(self):
        self.log.debug("Flushing All Cache Tokens")
        return self._getMemcacheProtocol().flushAll()


    @classmethod
    def reset(cls):
        """
        Reset the memory cachers
        """
        cls.memoryCacheInstance = {True: None, False: None}
