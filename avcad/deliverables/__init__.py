"""交付物生成包（D14 线缆清册 / D03 端子表）。

本包把已有的 `Project` 数据模型投影成两份施工交付表：

- D14 线缆清册（cable schedule）：工程每一条 Connection 一行，含起止设备 /
  端口、信号类型、布线图层、主备角色、备注，以及基于坐标的线长估算。
- D03 端子表（terminal block table）：按设备聚合其全部 ConcretePort，并标注
  每个端口连接到的对端设备，便于端子排接线与核对。

设计原则：零新增数据字段——只读取 `avcad.model.schema` 中已存在的属性，
所有展示信息均来自既有的 DeviceInstance / ConcretePort / Connection。
"""
