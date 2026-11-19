# Descriptive concept source excerpt

[Source paragraph 1; # 1）牛顿-欧拉法; intervening text may be omitted]

牛顿-欧拉法要求写出每个刚体的动力学方程，刚体之间约束力的存在，使得导出的动力学方程含有大量的、不需要的未知理想约束反力，这就必须提出便于计算机识别的刚体联系情况和约束形式的程序化方法，并自动消除约束反力。德国斯图加特大学力学B研究所的学者Wenner Schiehlen在列写系统的牛顿-欧拉方程以后，将笛卡儿广义坐标变换成独立变量，对完整约束系统用D'Alembert原理消除约束反力，对非完整约束系统用Jourdain原理消除约束反力，最后得到与系统自由度数目相同的动力学方程。Schiehlen还用FORTRAN语言编制了符号推导的计算机程序NEWEUL，可以在计算机上获得运动微分方程的显式表达式。完整约束系统消除约束反力的动力学方程为

[Source paragraph 2; # 2）拉格朗日方程法; intervening text may be omitted]

拉格朗日方程是关于约束力学系统的动力学方程，它有两种形式：一种是第一类拉格朗日方程，用直角坐标表示带有不定乘子的微分方程，既适用于完整系统，又适用于线性非完整系统；另一种是第二类拉格朗日方程，用广义坐标表示的微分方程，只适用于完整系统。由于多刚体系统十分复杂，在建立系统的动力学方程时，采用独立的拉格朗日广义坐标将十分困难，而采用不独立的笛卡儿广义坐标则比较方便。

[Source paragraph 3; # 2）拉格朗日方程法; intervening text may be omitted]

对于具有多余坐标的完整约束系统或非完整约束系统，用带乘子的拉格朗日方程处理是一种规格化的方法。导出的以笛卡儿广义坐标为变量的动力学方程是与广义坐标数目相同，且带乘子的微分方程，同时需要补充广义坐标的代数约束方程才能封闭。

[Source paragraph 4; # 2）拉格朗日方程法; intervening text may be omitted]

用拉格朗日方程法建立的多刚体系统的动力学模型是混合的微分-代数方程组(DAE)，其特点是方程组数目相当大，而且微分方程常常是刚性的。求微分-代数方程组的数值解算法研究是多刚体系统动力学的一个重要问题。

[Source paragraph 5; # 2）拉格朗日方程法; intervening text may be omitted]

蔡斯（Chace）等人选取系统内每个刚体质心在惯性参考系中的3个直角坐标和确定刚体方位的3个欧拉角作为笛卡儿广义坐标，应用吉尔（Gear）的刚性积分算法并采用稀疏矩阵技术提高计算效率，编制了计算机程序ADAMS（Automatic Dynamic Analysis of Mechanical Systems）。

