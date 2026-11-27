# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Team quota, usage and cost features (custom extension of ParallelCluster UI)."""
from api.teams.scope import TEAM_GROUP_PREFIX
from api.teams.lifecycle_store import MEMBER_GROUP_PREFIX
from api.teams.routes import team_quota
from api.teams import member_routes  # noqa: F401  (registers the /member routes on the blueprint)
from api.teams import lead_routes  # noqa: F401  (registers the /lead routes on the blueprint)

__all__ = ["team_quota", "TEAM_GROUP_PREFIX", "MEMBER_GROUP_PREFIX"]
