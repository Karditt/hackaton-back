import { getScenario, saveScenario } from '../../api';

/** Суффикс копии сценария с площадками, добавленными по совету. */
const SITE_SUFFIX = '--site';

/** Суффиксы производных сценариев: копия с правками и копия с площадкой. */
const DERIVED = /(--custom|--site)+$/;

/** Префикс идентификаторов площадок, добавленных по совету. */
const ADVISED_PREFIX = 'advised-site';

/** Пометка в имени копии. */
const NAME_MARK = ' (площадка по совету)';

/**
 * Принять проверенный совет «площадка»: копия посчитанного сценария с новой
 * штатной площадкой — `<сценарий>--site`. Копия берётся с того сценария, что
 * ушёл в расчёт (с правками панели), поэтому совет проверен ровно на нём.
 * Повторный совет добавляет площадку в ту же копию. Исходный сценарий не
 * меняется. Место — из выгрузки плана, в WGS84: веб ничего не пересчитывает.
 *
 * @param computedId сценарий, по которому посчитан план (возможно, `--custom`)
 * @param lon долгота места, градусы WGS84
 * @param lat широта места, градусы WGS84
 * @returns идентификатор копии с площадкой
 */
export async function addAdvisedSite(computedId: string, lon: number, lat: number) {
  const source = await getScenario(computedId);
  const advised = source.sites.filter((site) => site.id.startsWith(ADVISED_PREFIX)).length;
  const number = advised + 1;
  const name = (source.name || computedId).replace(NAME_MARK, '');
  const saved = await saveScenario({
    ...source,
    id: `${computedId.replace(DERIVED, '')}${SITE_SUFFIX}`,
    name: `${name}${NAME_MARK}`,
    sites: [
      ...source.sites,
      {
        id: `${ADVISED_PREFIX}-${number}`,
        name: number === 1 ? 'По совету' : `По совету ${number}`,
        position: { lon, lat },
        kind: 'primary',
      },
    ],
  });
  return saved.id;
}
