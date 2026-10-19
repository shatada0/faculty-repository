"""Все модели в одной metadata. Не удалять: без него связи между доменами не найдут классы по имени."""

from shared.auth import models as auth_models  # noqa: F401
from shared.imports import models as imports_models  # noqa: F401
from shared.people import models as people_models  # noqa: F401
from shared.refdata import models as refdata_models  # noqa: F401
from shared.works import models as works_models  # noqa: F401
