# Issue与PR提交记录

提交人：郭庆（gq001-X），日期：2026年11月19日。

- 指定仓库：https://github.com/liulian1666/github.-.yiliao
- Issue：https://github.com/liulian1666/github.-.yiliao/issues/1
- PR：https://github.com/liulian1666/github.-.yiliao/pull/2
- 修复提交：dbd3a414a3e7396b5f0322d4f2ac1e4ce35b3d60
- 状态：Open，尚未合并；维护者审核中。

问题是参数校验与HTTP异常未遵守统一返回结构。修复两个文件，新增106行，保留HTTP状态和协议响应头，不回显敏感输入。

同一组8个回归用例：原版本6失败、2通过；修复后8通过。命令为在docs2目录运行 `python -m unittest discover -s tests -v`。真实测试日志及页面截图见evidence目录，完整Word报告为《Issue与PR提交记录.docx》。
