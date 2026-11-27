# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Tiny in-process TTL cache. Each Lambda execution environment keeps its own copy."""
import threading
import time

_MISSING = object()


class TTLCache:
    def __init__(self, ttl_seconds, max_entries=256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            item = self._data.get(key, _MISSING)
            if item is _MISSING:
                return default
            expires, value = item
            if expires < time.monotonic():
                del self._data[key]
                return default
            return value

    def set(self, key, value):
        with self._lock:
            if len(self._data) >= self.max_entries:
                self._data.clear()
            self._data[key] = (time.monotonic() + self.ttl, value)
        return value

    def get_or_compute(self, key, compute):
        value = self.get(key, _MISSING)
        if value is _MISSING:
            value = self.set(key, compute())
        return value

    def clear(self):
        with self._lock:
            self._data.clear()
