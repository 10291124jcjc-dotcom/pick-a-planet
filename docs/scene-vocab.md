# 场景写法（episode.json 里的 set / scene）

坐标单位是米。Z 朝上，地面 z = 0。默认机位在 -Y 那边往 +Y 看，所以「正前方」是 -Y。道具的正面都朝 -Y。
`rot` 是绕竖轴转多少度：0 朝镜头，180 背对镜头，90 朝 +X（画面右边），-90 朝 -X。

## 场景顶层

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `time` | `day` `sunset` `dusk` `night` `snow` `overcast` `indoor` | `day` |
| `sky` | 天空渐变 `[底色, 顶色]`，覆盖 time 自带的 | — |
| `ground` | `grass` `snow` `sand` `tile` `wood` `dirt` `stone` `meadow` `none`，或直接写颜色 `#f7d9e4` | `grass` |
| `ground_color` | 地面颜色覆盖 | — |
| `ground_z` | 地面高度（做「水退下去露出海底」时把地面放到 -1.8） | 0 |
| `bumps` | 地面起伏（云海写 2 左右） | 0.35，`tile`/`wood` 是 0 |
| `water` | `{"z": 0, "col": "#3d5f9c"}` 一整片会反光的水；写 `null` 去掉 | 无 |
| `stars` | 3D 星星，`night` 默认开 | — |
| `glow` | 房子窗户、灯塔、路灯亮不亮，`dusk`/`night`/`indoor` 默认亮 | — |
| `ambient_strength` | 环境光强弱（太暗就调大） | 看 time |
| `sun` / `sun_energy` / `sun_elev` / `sun_az` / `sun_color` | 太阳开关、强度、高度角、方位角、颜色 | 看 time |
| `props` | 道具列表，见下 | [] |
| `camera` | 机位，见下 | 对准 hero |
| `fx` | `snow` `fireflies` `rain` `dust`（2D 叠加） | [] |
| `anim` | 动画，见下 | — |
| `replace` | `true`：这个镜头不用这颗星球的 set | — |

## 道具（props 里的每一项）

通用键：`type`（必填）、`id`（给机位和动画用）、`at: [x, y, z]`、`rot`（度）、`s`（缩放）、
`scatter`（批量撒）、`light: [[[dx,dy,dz], "#颜色", 瓦数], ...]`（手动加灯）。其余键都是这个道具自己的参数。

| type | 参数（都有默认值） | 高 |
| --- | --- | --- |
| `house` 带门廊的农舍 | `wall` `roof` `trim` `porch` `w` `d` `h` `porch_on` `chimney` `snow` `glow` | 5 |
| `cabin` 木屋 | `wood` `roof` `w` `d` `h` `snow` `glow` | 4 |
| `lighthouse` 灯塔 | `body` `stripe` `h` `glow`（门边自带一盏小灯） | 11 |
| `island` 小岛（沙 + 草，往下一直到海底） | `r` `sand` `grass`；岛上东西放在 z = 0.55 | 0.6 |
| `pine` 松树 | `h` `col` `snow` | 5 |
| `tree` 圆树 | `h` `col` | 4 |
| `bush` 灌木 | `s` `col` | 0.8 |
| `rock` 石头 | `s` `col` `snow` | 0.6 |
| `mountain` 山 | `h` `r` `col` `snow`（雪顶） | 40 |
| `flower` 花（向日葵等） | `h` `petal` `center` `head` | 2 |
| `mushroom` 大蘑菇 | `h` `cap` `dots` `stem` `glow` | 3 |
| `cloud` 云 | `s` `col` | — |
| `moon` / `sun` 月亮、太阳（自发光球，放远处） | `r` `col` | — |
| `fence` 栅栏（沿 +X 排） | `n` `col` | 1 |
| `dock` 码头（从 at 往 -Y 伸出去，板面高 0.56） | `l` `w` `col` | 0.6 |
| `boat` 小船 | `col` `l` | 0.4 |
| `lamp` 路灯（亮时自带点光） | `h` `col` `glow` | 3.6 |
| `table_lamp` 台灯（放桌上 z = 0.8） | `col` `glow` | 0.6 |
| `bed` 床 | `sheet` `blanket` `wood` | 1.1 |
| `table` 桌子（桌面高 0.8） | `col` `w` `d` | 0.8 |
| `chair` 扶手椅（座面高 0.55） | `col` | 1.2 |
| `mug` 杯子 | `col` `fill` | 0.13 |
| `bookshelf` 书架 | `w` `h` `wood` `seed` | 3.2 |
| `fireplace` 壁炉（自带火光） | `stone` `fire` | 4.5 |
| `snowman` 雪人 | `s` `scarf` | 2.3 |
| `balloon` 热气球（篮子底在 at，篮子高 0.9） | `col` `stripe` `basket` `r` | 10 |
| `footprints` 一串脚印（从 at 往 -Y 排） | `n` `col` `step` | — |
| `window` 独立窗框 | `w` `h` `frame` `glass` `glow` | 1.8 |
| `room` 室内（地板 + 左右后三面墙 + 后墙窗） | `w` `d` `h` `wall` `floor` `window` `outside`（窗外颜色）`ceiling` | 3.2 |
| `crate` 木箱 | `s` `col` | 0.8 |
| `pool` 温泉池（石头围一圈，`level` 0 到 1 是水位） | `r` `water` `rock` `level` `snow` | 0.5 |
| `steam` 一柱蒸汽 | `h` `col` | 6 |
| `bucket` 水桶（里面有鱼） | `col` `fish` | 0.36 |
| `openbook` 摊开的书 | `glow` `cover` `page` | 0.06 |
| `text` 立体发光字（招牌） | `text` `size` `col` `glow` `rot3` | — |
| `hero` 主角（见下） | | 1.8 |
| `mannequin` 白色假人（带底座） | 同 hero | 1.9 |

### 主角 hero

`{"type": "hero", "id": "hero", "at": [x, y, z], "pose": "back", "face": 180}`

- `pose`：`stand` 站、`back` 背对镜头站（默认朝 +Y）、`walk` 走（静帧是迈步的样子）、`sit` 坐、`read` 坐着看书（手里自动有书）、
  `wave` 招手、`point` 指、`swim` 泡在水里（两臂张开）、`lie` 躺（头朝 +Y）。
- `face`：朝向（度），0 朝镜头。
- 坐：`at` 的 z 写座位下面那层的高度。坐在椅子 / 地上写 0；坐在码头边写 0.14（码头板面 0.56）。
- 站在岛上 z = 0.55；站在热气球篮子里 z = 气球 z + 0.05。
- `pack: false` 去掉背包；`cols` 改衣服颜色：`{"shirt": "#..", "pants": "#..", "skin": "#..", "hair": "#.."}`。

### 批量撒 scatter

```json
{"type": "pine", "snow": true, "scatter": {"n": 150, "r": [14, 80], "center": [0, 6], "s": [0.9, 1.7]}}
```

- 圆环：`center`、`r: [内半径, 外半径]`、`arc: [起始度, 结束度]`；方框：`box: [x0, y0, x1, y1]`。
- `s: [最小, 最大]` 随机缩放；`z` 高度；`seed` 换一种排法；`avoid: [[x, y, 半径], ...]` 空出一块（主角、房子、镜头前面）。
- `face: 180` 所有东西大致朝同一个方向（雪人一起盯着窗户）；默认随机转。
- `grid: 1.0` 排成一行行（田地）。

## 机位 camera

```json
{"aim": "lh", "dist": 17, "yaw": -24, "pitch": 4, "lens": 28, "aim_z": 5.5}
```

- `aim`：对准某个 `id`（或直接写坐标 `[x, y, z]`）；默认对准 hero。
- `aim_z`：看这个道具离它的底多高的地方（不是绝对高度）。人脸约 1.6，坐着约 1.0，灯塔中段约 5。
- `dist` 多远；`yaw` 从哪边看（0 正前方，-30 偏左前，90 从右边，180 从背后）；`pitch` 机位高度角（负数是从下往上看）。
- `lens`：18 到 24 广角（大场景、室内），28 到 32 常规，35 以上特写。
- 也可以直接写 `"pos": [x, y, z], "look": [x, y, z]`。

参考镜头：
- 大全景（这颗星球是一整片……）：`dist` 25 到 45，`pitch` 6 到 14，`lens` 28 到 30。
- 主角 + 环境：`dist` 4 到 7，`aim_z` 1.0 到 1.5。
- 规矩那句：近景，`dist` 1.3 到 5，`push` 0.12 到 0.16。

## 动画 anim（这个镜头会渲染一串帧）

```json
"anim": [
  {"id": "water", "z": [[0.3, 0], [3.6, -2.3]]},
  {"id": "hero", "move": [[0, 0, 0, 0], [4.5, 0, 3.4, 0]], "walk": true},
  {"id": "sm", "scale": [[0, 0.2], [2.0, 1.0]]},
  {"id": "bal", "move": [[0, 0, 0, 0], [4, 0, 0, 3]]}
]
```

- 每个关键帧是 `[秒, 值...]`，秒从这个镜头开始算，中间平滑过渡。
- `z`：上下移动（相对原位）；`move`：`[秒, dx, dy, dz]` 平移；`loc`：绝对坐标；`scale`：整体缩放倍数；`spin`：绕竖轴转到多少度。
- `walk: true`：主角边移动边迈腿摆臂（方向要和 `face` 一致）。
- `id: "water"` 指整片水，`id: "camera"` 可以移动机位。
- 一个 4 秒的动镜头大约 60 帧，渲染约半分钟。

## 星球样子 globe（标题卡和开头那 4 颗）

```json
{"pattern": "noise", "base": "#2a66b0", "spots": "#e8d39a", "third": "#7fae5a", "amount": 0.6, "scale": 3.5,
 "ring": true, "ring_col": "#f3c64a", "moon": true, "spikes": 6, "glow": 0.5, "tilt": 18}
```

- `pattern`：`noise` 大陆和海、`bands` 一圈圈条纹、`tiles` 格子、`dots` 一颗颗亮点（萤火虫、城市灯光）。
- `amount` 越大 `base` 占得越多；`third` 加第三种颜色；`ring` 土星环；`moon` 一颗小卫星绕着转；`spikes` 往外喷的柱子（间歇泉）；`glow` 自发光。
